#!/usr/local/bin/python3
# coding: utf-8

import asyncio
import binascii
import inspect
import logging
import time
import weakref
from textwrap import wrap

from bleak import (BleakClient, BleakError)
from bleak_retry_connector import BleakOutOfConnectionSlotsError, establish_connection
from homeassistant.components import bluetooth

from .r4sconst import SUPPORTED_DEVICES

_LOGGER = logging.getLogger(__name__)

UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

_GLOBAL_RADIO_SEM: asyncio.Semaphore | None = None
_GLOBAL_CLIENTS: "weakref.WeakSet[BLEReady4SkyClient]" = weakref.WeakSet()
_GLOBAL_LAST_DISCONNECT_TS = 0.0
_GLOBAL_CONNECT_SETTLE_SEC = 0.8


class BusyConnectionError(BleakError):
    """Transient non-failure state: another connect flow is in progress."""


def _get_global_radio_sem() -> asyncio.Semaphore:
    global _GLOBAL_RADIO_SEM
    if _GLOBAL_RADIO_SEM is None:
        _GLOBAL_RADIO_SEM = asyncio.Semaphore(1)
    return _GLOBAL_RADIO_SEM


def _seconds_since_last_disconnect() -> float:
    return time.monotonic() - _GLOBAL_LAST_DISCONNECT_TS


class BLEReady4SkyClient:
    def __init__(self, hass, mac, key, name=None):
        self._name = (name or "").strip()
        self._type = SUPPORTED_DEVICES.get(self._name)
        self._hass = hass
        self._mac = mac
        self._key = key
        self._iter = 0
        self._callbacks = {}
        self._afterConnectCallback = None
        self._conn = None
        self._device = None
        self._available = False
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._notifications_enabled = False
        self._session_users = 0
        self._holds_global_radio = False
        self._radio_waiters = 0
        self._user_waiters = 0
        self._disconnect_done = asyncio.Event()
        self._disconnect_done.set()
        self._disconnect_task: asyncio.Task | None = None
        self._disconnect_delay = 300
        self._backoff_seconds = 0
        self._max_backoff_seconds = 20
        self._next_connect_ts = 0.0
        self._connection_epoch = 0
        self._last_activity = time.monotonic()
        self._connect_fail_streak = 0
        self._connect_fail_total = 0
        self._connect_success_total = 0
        self._last_connect_ok_ts = 0.0
        self._last_connect_fail_ts = 0.0
        self._avg_connect_ms = 0.0
        _GLOBAL_CLIENTS.add(self)

    async def _session_users_count(self) -> int:
        async with self._session_lock:
            return self._session_users

    async def _wait_and_disconnect_peers(self, blocking: bool) -> None:
        """Ensure no other Ready4Sky client keeps active BLE link while we connect."""
        deadline = time.monotonic() + 3.0 if blocking else time.monotonic()
        while True:
            busy_peers: list[str] = []
            disconnected_any = False
            for peer in list(_GLOBAL_CLIENTS):
                if peer is self:
                    continue
                conn = peer._conn
                if conn is None or not conn.is_connected:
                    continue
                # Poll/background connect must not steal radio from connected peer.
                # Handoff is allowed only for blocking (user) operations.
                if not blocking:
                    raise BusyConnectionError(f"Peer holds BLE link: {peer._mac}")
                peer_users = await peer._session_users_count()
                if (
                    peer_users > 0
                    or peer._user_waiters > 0
                    or peer._write_lock.locked()
                    or peer._connect_lock.locked()
                ):
                    busy_peers.append(peer._mac)
                    continue
                _LOGGER.debug(
                    "Disconnecting peer %s before connecting %s (single-radio mode)",
                    peer._mac,
                    self._mac,
                )
                await peer.disconnect()
                disconnected_any = True
            if not busy_peers:
                return
            if not blocking:
                raise BusyConnectionError(f"Peer is busy: {', '.join(busy_peers)}")
            if time.monotonic() >= deadline:
                raise BleakError(f"Timed out waiting for peer session to finish: {', '.join(busy_peers)}")
            if not disconnected_any:
                await asyncio.sleep(0.1)

    def _has_peer_radio_waiters(self) -> bool:
        for peer in list(_GLOBAL_CLIENTS):
            if peer is self:
                continue
            # Immediate handoff should favor queued user commands, not poll waiters.
            if peer._radio_waiters > 0 and peer._user_waiters > 0:
                return True
        return False

    def set_user_waiters(self, count: int) -> None:
        self._user_waiters = max(0, int(count))

    def _has_peer_user_waiters(self) -> bool:
        for peer in list(_GLOBAL_CLIENTS):
            if peer is self:
                continue
            if peer._user_waiters > 0:
                return True
        return False

    def _get_ble_device(self):
        device = bluetooth.async_ble_device_from_address(self._hass, self._mac, True)
        if device is None:
            # Fallback to any known advertisement record for metadata.
            device = bluetooth.async_ble_device_from_address(self._hass, self._mac, False)
        return device

    async def _resolve_ble_device(self, attempts: int = 1, delay: float = 1.0):
        for i in range(attempts):
            device = self._get_ble_device()
            if device is not None:
                return device
            if i < attempts - 1:
                await asyncio.sleep(delay)
        return None

    async def resolveIdentity(self, refresh: bool = False) -> bool:
        # Use cached name as primary identity source; only query scanner when needed.
        if self._name and not refresh:
            self._type = SUPPORTED_DEVICES.get(self._name)
            if self._type is not None:
                self._available = True
                return True

        # After HA restart scanner cache can be empty for a few seconds.
        self._device = await self._resolve_ble_device(attempts=8, delay=1.0) or self._device
        if self._device is None:
            self._available = False
            _LOGGER.debug('Device "%s" not found on bluetooth network', self._mac)
            return self._type is not None

        resolved_name = (getattr(self._device, "name", None) or self._name or "").strip()
        if not resolved_name:
            self._available = False
            _LOGGER.debug('Device "%s" has no BLE name in advertisements yet', self._mac)
            return self._type is not None

        self._name = resolved_name
        self._type = SUPPORTED_DEVICES.get(self._name)
        if self._type is None:
            self._available = False
            _LOGGER.error('Device "%s" not supported. Please report developer or view file r4sconst.py', self._name)
            return False

        self._available = True
        return True

    def _seconds_until_next_attempt(self) -> int:
        return max(0, int(self._next_connect_ts - time.monotonic()))

    def _schedule_backoff(self):
        self._backoff_seconds = 5 if self._backoff_seconds == 0 else min(self._backoff_seconds * 2, self._max_backoff_seconds)
        self._next_connect_ts = time.monotonic() + self._backoff_seconds
        _LOGGER.debug(
            "BLE reconnect backoff for %s set to %ss",
            self._mac,
            self._backoff_seconds,
        )

    def _reset_backoff(self):
        self._backoff_seconds = 0
        self._next_connect_ts = 0.0

    def _record_connect_success(self, duration_s: float):
        self._connect_success_total += 1
        self._connect_fail_streak = 0
        self._last_connect_ok_ts = time.time()
        connect_ms = duration_s * 1000.0
        # EMA keeps the metric stable but still responsive to regression.
        if self._avg_connect_ms <= 0:
            self._avg_connect_ms = connect_ms
        else:
            self._avg_connect_ms = (self._avg_connect_ms * 0.8) + (connect_ms * 0.2)

    def _record_connect_failure(self):
        self._connect_fail_total += 1
        self._connect_fail_streak += 1
        self._last_connect_fail_ts = time.time()

    async def __aenter__(self):
        return await self.acquire_session(blocking=True)

    async def acquire_session(self, blocking: bool = True):
        if self._disconnect_task is not None and not self._disconnect_task.done():
            self._disconnect_task.cancel()
            self._disconnect_task = None

        started = time.monotonic()
        attempts = 1 if not blocking else 1000000
        for _ in range(attempts):
            acquired_radio = False
            adopted_radio = False
            already_holds_radio = False
            waiting_for_radio = False
            try:
                async with self._session_lock:
                    already_holds_radio = self._holds_global_radio
                    if self._session_users > 0 and self._conn is not None and self._conn.is_connected:
                        self._last_activity = time.monotonic()
                        self._session_users += 1
                        _LOGGER.debug(
                            "BLE session acquired for %s in %.3fs (blocking=%s, users=%s)",
                            self._mac,
                            time.monotonic() - started,
                            blocking,
                            self._session_users,
                        )
                        return self

                if not already_holds_radio:
                    radio_sem = _get_global_radio_sem()
                    if not blocking and self._has_peer_user_waiters():
                        raise BusyConnectionError("Peer has pending user operations")
                    self._radio_waiters += 1
                    waiting_for_radio = True
                    if blocking:
                        await radio_sem.acquire()
                        acquired_radio = True
                    else:
                        try:
                            await asyncio.wait_for(radio_sem.acquire(), timeout=0.001)
                            acquired_radio = True
                        except asyncio.TimeoutError as ex:
                            raise BusyConnectionError("Global radio slot is busy") from ex

                await self.ensure_connected(blocking=blocking)
                async with self._session_lock:
                    if self._conn is not None and self._conn.is_connected:
                        self._last_activity = time.monotonic()
                        self._session_users += 1
                        if acquired_radio:
                            # This session now owns global radio until users drop to 0.
                            self._holds_global_radio = True
                            adopted_radio = True
                        _LOGGER.debug(
                            "BLE session acquired for %s in %.3fs (blocking=%s, users=%s)",
                            self._mac,
                            time.monotonic() - started,
                            blocking,
                            self._session_users,
                        )
                        return self
            except BusyConnectionError:
                raise
            except Exception:
                raise
            finally:
                if waiting_for_radio:
                    self._radio_waiters = max(0, self._radio_waiters - 1)
                if acquired_radio and not adopted_radio:
                    _get_global_radio_sem().release()
            if not blocking:
                break
            # Avoid tight spin loop when connection is unstable.
            await asyncio.sleep(0.05)

        raise BleakError(f"Unable to acquire BLE session for {self._mac}")

    async def ensure_connected(self, blocking: bool = True):
        if self._type is None:
            if not await self.resolveIdentity():
                if not blocking:
                    raise BusyConnectionError(f"Device identity is not resolved yet: {self._mac}")
                raise BleakError(f"Device identity is not resolved yet: {self._mac}")

        # Fast path for background polling: do not start connect flow when the device
        # is not visible in HA BLE network cache. This reduces contention for multi-device setups.
        if not blocking:
            quick_device = self._get_ble_device()
            if quick_device is None:
                _LOGGER.debug(
                    "Skipping non-blocking connect for %s: device is not visible in HA BLE cache",
                    self._mac,
                )
                raise BusyConnectionError(f"Device is not visible in HA BLE cache: {self._mac}")

        async with self._connect_lock:
            settle_left = _GLOBAL_CONNECT_SETTLE_SEC - _seconds_since_last_disconnect()
            if settle_left > 0:
                if not blocking:
                    raise BusyConnectionError(
                        f"Radio settle in progress ({settle_left:.2f}s after disconnect)"
                    )
                await asyncio.sleep(settle_left)

            if not self._disconnect_done.is_set():
                if not blocking:
                    raise BusyConnectionError("Disconnect in progress")
                try:
                    await asyncio.wait_for(self._disconnect_done.wait(), timeout=5.0)
                except asyncio.TimeoutError as ex:
                    raise BleakError(f"Timed out waiting disconnect completion for {self._mac}") from ex

            is_connected = self._conn is not None and self._conn.is_connected
            if is_connected:
                if not self._notifications_enabled:
                    await self.enableNotification()
                return self

            wait_seconds = self._seconds_until_next_attempt()
            if wait_seconds > 0 and not blocking:
                _LOGGER.debug(
                    "Skipping non-blocking connect for %s due to reconnect backoff (%ss left)",
                    self._mac,
                    wait_seconds,
                )
                raise BusyConnectionError(f"Reconnect backoff is active for {wait_seconds}s")

            try:
                # BlueZ is sensitive to concurrent connect attempts across devices.
                await self._wait_and_disconnect_peers(blocking=blocking)
                connect_started = time.monotonic()
                self._device = await self._resolve_ble_device(attempts=12, delay=1.0) or self._device
                if self._device is not None:
                    self._connection_epoch += 1
                    epoch = self._connection_epoch
                    self._conn = await establish_connection(
                        BleakClient,
                        self._device,
                        self._name or self._mac,
                        max_attempts=2,
                        disconnected_callback=lambda client: self._handle_disconnect(client, epoch),
                    )
                else:
                    _LOGGER.debug(
                        "Device %s is not available in HA BLE cache yet, trying direct address connect",
                        self._mac,
                    )
                    self._connection_epoch += 1
                    epoch = self._connection_epoch
                    self._conn = BleakClient(
                        self._mac,
                        disconnected_callback=lambda client: self._handle_disconnect(client, epoch),
                    )
                    await self._conn.connect()
                connect_duration = time.monotonic() - connect_started
                _LOGGER.debug("BLE connected to %s in %.3fs", self._mac, connect_duration)
                self._record_connect_success(connect_duration)

                self._available = True
                self._reset_backoff()
                notify_started = time.monotonic()
                await self.enableNotification()
                _LOGGER.debug("Notifications enabled for %s in %.3fs", self._mac, time.monotonic() - notify_started)
                await self.connectAfter()
                return self
            except BleakOutOfConnectionSlotsError as ex:
                _LOGGER.warning("No BLE connection slots available for %s: %s", self._mac, ex)
                self._available = False
                self._record_connect_failure()
                self._schedule_backoff()
                raise
            except BusyConnectionError:
                raise
            except BleakError as ex:
                self._available = False
                _LOGGER.debug("Device %s is not available for connection yet: %s", self._mac, ex)
                self._record_connect_failure()
                self._schedule_backoff()
                await self.disconnect()
                raise
            except Exception as ex:
                _LOGGER.error('Unable to connect')
                _LOGGER.exception(ex)
                self._record_connect_failure()
                self._schedule_backoff()
                await self.disconnect()
                raise ex

        return self

    def _handle_disconnect(self, _client, epoch: int):
        if epoch != self._connection_epoch:
            return

        self._available = False
        self._notifications_enabled = False
        self._conn = None

    async def enableNotification(self):
        if self._conn is None:
            return

        try:
            await self._conn.get_services()
        except Exception:
            pass

        # On BlueZ + AcquireNotify direct CCCD writes are rejected. Let Bleak/BlueZ
        # handle enabling notifications internally via start_notify.
        try:
            await self._conn.start_notify(UART_TX_CHAR_UUID, self.handleNotification)
            self._notifications_enabled = True
        except BleakError as ex:
            message = str(ex)
            # BlueZ may report NotPermitted/Notify acquired when notifications are
            # already active on this characteristic. Treat as success and continue.
            if "Notify acquired" in message or "NotPermitted" in message:
                self._notifications_enabled = True
                _LOGGER.debug(
                    "Notifications already active for %s (%s), using existing AcquireNotify session",
                    self._mac,
                    message,
                )
                return
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release_session()

    async def release_session(self):
        should_schedule_disconnect = False
        release_global_radio = False
        handoff_disconnect_now = False
        async with self._session_lock:
            if self._session_users > 0:
                self._session_users -= 1
            should_schedule_disconnect = self._session_users == 0
            if should_schedule_disconnect and self._holds_global_radio:
                # Fast handoff: if another device is waiting for radio, release this
                # link immediately instead of waiting disconnect_delay.
                if self._has_peer_radio_waiters():
                    handoff_disconnect_now = True
                else:
                    self._holds_global_radio = False
                    release_global_radio = True
            users = self._session_users

        if handoff_disconnect_now:
            _LOGGER.debug("Immediate radio handoff from %s: peer waiter detected", self._mac)
            await self.disconnect()
            async with self._session_lock:
                if self._holds_global_radio:
                    self._holds_global_radio = False
            _get_global_radio_sem().release()
            return

        if release_global_radio:
            _get_global_radio_sem().release()

        if should_schedule_disconnect and (self._disconnect_task is None or self._disconnect_task.done()):
            _LOGGER.debug("Scheduling delayed disconnect for %s in %ss", self._mac, self._disconnect_delay)
            self._disconnect_task = asyncio.create_task(self._delayed_disconnect())
        else:
            _LOGGER.debug("BLE session released for %s (users=%s)", self._mac, users)

    async def _delayed_disconnect(self):
        try:
            while True:
                remaining = self._disconnect_delay - (time.monotonic() - self._last_activity)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                async with self._session_lock:
                    if self._session_users > 0:
                        return
                    if (time.monotonic() - self._last_activity) >= self._disconnect_delay:
                        break
            await self.disconnect()
        except asyncio.CancelledError:
            return

    @staticmethod
    async def getDiscoverDevices(hass):
        devices = await bluetooth.async_get_scanner(hass).discover()

        return {str(device.address): str(device.name) for device in devices}

    async def disconnect(self):
        global _GLOBAL_LAST_DISCONNECT_TS
        try:
            current_task = asyncio.current_task()
            if (
                self._disconnect_task is not None
                and not self._disconnect_task.done()
                and self._disconnect_task is not current_task
            ):
                self._disconnect_task.cancel()
            self._disconnect_task = None
            self._disconnect_done.clear()
            conn = self._conn
            self._connection_epoch += 1
            self._conn = None
            self._notifications_enabled = False

            if conn is not None and conn.is_connected:
                # With AcquireNotify BlueZ closes the notify FD on disconnect; explicit
                # stop_notify is unnecessary and often raises. Simply disconnect.
                await conn.disconnect()
                _GLOBAL_LAST_DISCONNECT_TS = time.monotonic()

            self._iter = 0
        except asyncio.CancelledError:
            # Can happen during connect/disconnect race in BlueZ; treat as benign.
            _LOGGER.debug("disconnect cancelled for %s", self._mac)
        except Exception as ex:
            self._available = False
            _LOGGER.error('disconnect failed')
            _LOGGER.exception(ex)
        finally:
            self._disconnect_done.set()

    def handleNotification(self, handle, data):
        arrData = wrap(binascii.b2a_hex(data).decode("utf-8"), 2)
        respType = arrData[2]

        _LOGGER.debug('NOTIF: handle: %s cmd: %s full: %s', str(handle), str(respType), str(arrData))

        if respType in self._callbacks:
            self._callbacks[respType](arrData)

    @property
    def mac(self):
        return self._mac

    def setCallback(self, respType, function):
        self._callbacks[str(respType)] = function

    async def makeRequest(self, value):
        cmd = wrap(value, 2)
        _LOGGER.debug('MAKE REQUEST: cmd %s, full %s', cmd[2], cmd)

        async with self._write_lock:
            self._last_activity = time.monotonic()
            if self._conn is None or not self._conn.is_connected:
                await self.ensure_connected()

            try:
                await self._conn.write_gatt_char(UART_RX_CHAR_UUID, binascii.a2b_hex(bytes(value, 'utf-8')), True)
                return True
            except BleakError as ex:
                _LOGGER.error('not send request %s', inspect.getouterframes(inspect.currentframe(), 2)[1][3])
                _LOGGER.exception(ex)
                self._notifications_enabled = False

        return False

    async def sendRequest(self, cmdHex, dataHex=''):
        ok, _iter_hex = await self.sendRequestWithIter(cmdHex, dataHex)
        return ok

    async def sendRequestWithIter(self, cmdHex, dataHex=''):
        iter_hex = self.getHexNextIter()
        ok = await self.makeRequest('55' + iter_hex + str(cmdHex) + dataHex + 'aa')
        return ok, iter_hex

    @staticmethod
    def hexToDec(hexStr: str) -> int:
        return int.from_bytes(binascii.a2b_hex(bytes(hexStr, 'utf-8')), 'little')

    @staticmethod
    def decToHex(num: int) -> str:
        return num.to_bytes((num.bit_length() + 7) // 8, 'little').hex() or '00'

    def getHexNextIter(self) -> str:
        current = self._iter
        self._iter = 0 if self._iter > 254 else self._iter + 1

        return self.decToHex(current)

    async def connectAfter(self):
        if self._afterConnectCallback is not None:
            await self._afterConnectCallback(self)

    def setConnectAfter(self, func):
        self._afterConnectCallback = func

    def set_disconnect_delay(self, seconds: int) -> None:
        self._disconnect_delay = max(0, int(seconds))

    def health_snapshot(self) -> dict:
        return {
            "connect_fail_streak": self._connect_fail_streak,
            "connect_fail_total": self._connect_fail_total,
            "connect_success_total": self._connect_success_total,
            "last_connect_ok_ts": self._last_connect_ok_ts,
            "last_connect_fail_ts": self._last_connect_fail_ts,
            "avg_connect_ms": round(self._avg_connect_ms, 1),
            "backoff_seconds": self._backoff_seconds,
        }
