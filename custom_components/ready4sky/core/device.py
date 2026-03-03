"""Device/protocol implementation for Ready4Sky."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Callable

from bleak import BleakError

from .ble_client import BLEReady4SkyClient, BusyConnectionError
from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    MODE_BOIL,
    MODE_LIGHT,
    STATUS_OFF,
    STATUS_ON,
)

_LOGGER = logging.getLogger(__name__)

class Ready4SkyCommand(Enum):
    AUTH = 'ff'
    VERSION = '01'
    RUN_CURRENT_MODE = '03'  # sendOn
    STOP_CURRENT_MODE = '04'  # sendOff
    SET_STATUS_MODE = '05'  # sendMode
    GET_STATUS_MODE = '06'
    SET_DELAY = '08'
    SET_COLOR = '32'  # sendSetLights
    GET_COLOR = '33'  # sendGetLights
    SET_BACKLIGHT_MODE = '37'  # sendUseBacklight
    SET_SOUND = '3c'
    SET_LOCK_BUTTONS = '3e'
    GET_STATISTICS_WATT = '47'
    GET_STARTS_COUNT = '50'
    SET_TIME = '6e'  # sendSync
    SET_IONIZATION = '1b'
    SET_TEMPERATURE = '0b'
    SET_TIME_COOKER = '0c'

    def __str__(self):
        return str(self.value)


class Ready4SkyDevice:
    _GLOBAL_USER_COMMAND_UNTIL = 0.0
    _USER_COMMAND_WINDOW_SEC = 15.0
    _DUPLICATE_COMMAND_WINDOW_SEC = 0.8
    _QUEUED_DUPLICATE_COMMAND_WINDOW_SEC = 8.0

    def __init__(self, hass, addr, key, backlight, name=None):
        self.hass = hass
        self._type = None
        self._name = name
        self._mac = addr
        self._key = key
        self._use_backlight = backlight
        self._tgtemp = CONF_MIN_TEMP
        self._temp = None
        self._Watts = 0
        self._alltime = 0
        self._times = 0
        self._firmware_ver = None
        self._time_upd = '00:00'
        self._boiltime = '80'
        self._nightlight_brightness = 255
        self._rgb1 = (0, 0, 255)
        self._rgb2 = (255, 0, 0)
        self._mode = MODE_BOIL  # '00' - boil, '01' - heat to temp, '03' - backlight | for cooker 00 - heat after cook, 01 - off after cook | for fan 00-06 - speed
        self._status = '00'  # may be '00' - OFF or '02' - ON | for cooker 00 - off   01 - setup program   02 - on  04 - heat   05 - delayed start
        self._prog = '00'  # program
        self._sprog = '00'  # subprogram
        self._ph = 0  # program hours
        self._pm = 0  # program min
        self._th = 0  # timer hours
        self._tm = 0  # timer min
        self._ion = '00'  # 00 - off   01 - on
        self._conf_sound_on = False
        self._auth = False
        self._response_events: dict[tuple[str, str], asyncio.Event] = {}
        self._response_payloads: dict[tuple[str, str], list[str]] = {}
        self._conn = BLEReady4SkyClient(self.hass, self._mac, self._key, self._name)
        self._available = False
        self._state_callback: Callable[[dict], None] | None = None
        self._op_lock = asyncio.Lock()
        self._user_waiting = 0
        self._user_waiting_lock = asyncio.Lock()
        self._power_intent = "off"
        self._power_intent_seq = 0
        self._update_counter = 0
        self._last_push_ts = 0.0
        self._last_sync_ts = 0.0
        self._last_stat_ts = 0.0
        self._recent_command_ts: dict[str, float] = {}
        self._offline_streak = 0
        self._cold_mode = False
        self.initCallbacks()

    @property
    def logger(self):
        return _LOGGER

    def set_state_callback(self, callback: Callable[[dict], None]) -> None:
        self._state_callback = callback

    def export_state(self) -> dict:
        return {
            "type": self._type,
            "name": self._name,
            "mac": self._mac,
            "available": self._available,
            "target_temperature": self._tgtemp,
            "current_temperature": self._temp if self._available else None,
            "energy_wh": self._Watts,
            "work_hours": self._alltime,
            "times_started": self._times,
            "firmware_version": self._firmware_ver,
            "last_sync": self._time_upd,
            "nightlight_brightness": self._nightlight_brightness,
            "rgb1": self._rgb1,
            "rgb2": self._rgb2,
            "use_backlight": self._use_backlight,
            "mode": self._mode,
            "status": self._status,
            "program": self._prog,
            "subprogram": self._sprog,
            "program_hours": self._ph,
            "program_minutes": self._pm,
            "timer_hours": self._th,
            "timer_minutes": self._tm,
            "ionization": self._ion,
            "conf_sound_on": self._conf_sound_on,
            "auth": self._auth,
            "cold_mode": self._cold_mode,
            "last_push_ts": self._last_push_ts,
            "health": self._conn.health_snapshot(),
        }

    def _push_state_update(self):
        if self._state_callback is not None:
            self._state_callback(self.export_state())

    async def _has_user_waiting(self) -> bool:
        async with self._user_waiting_lock:
            return self._user_waiting > 0

    def _is_in_global_command_window(self) -> bool:
        return time.monotonic() < Ready4SkyDevice._GLOBAL_USER_COMMAND_UNTIL

    def _mark_user_command_window(self) -> None:
        Ready4SkyDevice._GLOBAL_USER_COMMAND_UNTIL = max(
            Ready4SkyDevice._GLOBAL_USER_COMMAND_UNTIL,
            time.monotonic() + Ready4SkyDevice._USER_COMMAND_WINDOW_SEC,
        )

    def _is_duplicate_command(self, command_key: str) -> bool:
        now = time.monotonic()
        prev = self._recent_command_ts.get(command_key, 0.0)
        self._recent_command_ts[command_key] = now
        # Coalesce only while another command is still in-flight.
        if self._op_lock.locked() and now - prev <= Ready4SkyDevice._DUPLICATE_COMMAND_WINDOW_SEC:
            _LOGGER.debug("Coalesced duplicate command for %s: %s", self._mac, command_key)
            return True
        # Under unstable BLE, UI can enqueue many identical retries; collapse them while queue exists.
        if self._user_waiting > 0 and now - prev <= Ready4SkyDevice._QUEUED_DUPLICATE_COMMAND_WINDOW_SEC:
            _LOGGER.debug("Coalesced duplicate command for %s: %s", self._mac, command_key)
            return True
        return False

    async def _set_power_intent(self, intent: str) -> int:
        async with self._user_waiting_lock:
            self._power_intent_seq += 1
            self._power_intent = intent
            return self._power_intent_seq

    async def _is_stale_power_intent(self, intent: str, seq: int) -> bool:
        async with self._user_waiting_lock:
            return self._power_intent != intent or self._power_intent_seq != seq

    @asynccontextmanager
    async def _user_operation(self):
        wait_started = time.monotonic()
        async with self._user_waiting_lock:
            self._user_waiting += 1
            queue_len = self._user_waiting
            self._conn.set_user_waiters(self._user_waiting)
        self._mark_user_command_window()
        _LOGGER.debug("User operation queued for %s (queue=%s)", self._mac, queue_len)
        try:
            async with self._op_lock:
                waited = time.monotonic() - wait_started
                if waited > 0.01:
                    _LOGGER.debug("User operation lock wait for %s: %.3fs", self._mac, waited)
                yield
        finally:
            async with self._user_waiting_lock:
                self._user_waiting = max(0, self._user_waiting - 1)
                queue_left = self._user_waiting
                self._conn.set_user_waiters(self._user_waiting)
            _LOGGER.debug("User operation done for %s (queue=%s)", self._mac, queue_left)

    @property
    def is_active(self) -> bool:
        return self._status in {"01", "02", "04", "05"}

    async def setNameAndType(self):
        await self._conn.resolveIdentity()
        self._type = self._conn._type
        self._name = self._conn._name
        self._available = self._conn._available
        self._push_state_update()

    async def disconnect(self):
        await self._conn.disconnect()

    def initCallbacks(self):
        self._conn.setConnectAfter(self.sendAuth)
        self._register_callback_with_ack(Ready4SkyCommand.AUTH, self.responseAuth)
        self._register_callback_with_ack(Ready4SkyCommand.VERSION, self.responseGetVersion)
        self._register_callback_with_ack(Ready4SkyCommand.RUN_CURRENT_MODE, self._responseAck)
        self._register_callback_with_ack(Ready4SkyCommand.STOP_CURRENT_MODE, self._responseAck)
        self._register_callback_with_ack(Ready4SkyCommand.SET_STATUS_MODE, self._responseAck)
        self._register_callback_with_ack(Ready4SkyCommand.GET_STATUS_MODE, self.responseStatus)
        self._register_callback_with_ack(Ready4SkyCommand.GET_STATISTICS_WATT, self.responseStat)
        self._register_callback_with_ack(Ready4SkyCommand.GET_STARTS_COUNT, self.responseStat)

    def _register_callback_with_ack(self, cmd: Ready4SkyCommand, handler: Callable[[list[str]], None]) -> None:
        cmd_key = str(cmd)

        def _wrapped(arr_hex: list[str]):
            handler(arr_hex)
            resp_iter = str(arr_hex[1]).lower() if len(arr_hex) > 1 else "00"
            self._mark_response(cmd_key, resp_iter, arr_hex)

        self._conn.setCallback(cmd, _wrapped)

    def _response_wait_key(self, cmd_key: str, iter_key: str) -> tuple[str, str]:
        return cmd_key, iter_key.lower()

    def _mark_response(self, cmd_key: str, iter_key: str, arr_hex: list[str]) -> None:
        key = self._response_wait_key(cmd_key, iter_key)
        event = self._response_events.get(key)
        if event is not None:
            self._response_payloads[key] = arr_hex
            event.set()

    async def _wait_response(self, cmd_key: str, iter_key: str, timeout: float) -> list[str] | None:
        key = self._response_wait_key(cmd_key, iter_key)
        event = self._response_events.setdefault(key, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._response_payloads.get(key)
        except asyncio.TimeoutError:
            return None

    async def _send_request_and_wait(
        self,
        conn,
        cmd: Ready4SkyCommand,
        data: str = "",
        timeout: float = 1.0,
        *,
        require_success: bool = False,
    ) -> bool:
        cmd_key = str(cmd)
        req_iter = conn.getHexNextIter()
        wait_key = self._response_wait_key(cmd_key, req_iter)
        self._response_events[wait_key] = asyncio.Event()
        response: list[str] | None = None
        try:
            ok = await conn.makeRequest('55' + req_iter + cmd_key + data + 'aa')
            if not ok:
                return False
            response = await self._wait_response(cmd_key, req_iter, timeout)
            ok = response is not None
            if ok and require_success:
                ok = self._is_success_ack(response)
                if not ok:
                    _LOGGER.debug(
                        "Negative ack for %s iter=%s on %s: %s",
                        cmd_key,
                        req_iter,
                        self._mac,
                        response,
                    )
        finally:
            self._response_events.pop(wait_key, None)
            self._response_payloads.pop(wait_key, None)
        if response is None:
            _LOGGER.debug("Timeout waiting response for %s iter=%s on %s", cmd_key, req_iter, self._mac)
        return ok

    @staticmethod
    def _is_success_ack(arr_hex: list[str] | None) -> bool:
        return bool(arr_hex and len(arr_hex) > 3 and arr_hex[3] == "01")

    def hexToRgb(self, hexa: str):
        return tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))

    def rgbToHex(self, rgb):
        return '%02x%02x%02x' % rgb

    @staticmethod
    def hexToDec(hexChr: str) -> int:
        return BLEReady4SkyClient.hexToDec(hexChr)

    @staticmethod
    def decToHex(num: int) -> str:
        return BLEReady4SkyClient.decToHex(num)

    def getHexNextIter(self) -> str:
        return self._conn.getHexNextIter()

    async def sendAuth(self, conn):
        self._type = conn._type
        self._name = conn._name

        self._auth = False
        if not await self._send_request_and_wait(conn, Ready4SkyCommand.AUTH, self._key, timeout=3.0):
            raise Exception('error auth timeout')

        if self._auth is False:
            raise Exception('error auth')

        return True

    def responseAuth(self, arrayHex):
        if self._type in [0, 1, 3, 4, 5] and arrayHex[3] == '01':
            self._auth = True
        elif self._type == 2 and arrayHex[3] == '02':
            self._auth = True
        else:
            self._auth = False

        return self._auth

    async def sendGetVersion(self, conn):
        return await self._send_request_and_wait(conn, Ready4SkyCommand.VERSION, timeout=1.0)

    def responseGetVersion(self, arrHex):
        self._firmware_ver = str(self.hexToDec(arrHex[3])) + '.' + str(self.hexToDec(arrHex[4]))
        self._push_state_update()

    async def sendOn(self, conn):
        if self._type == 0:
            return True

        if self._type in [1, 2, 3, 4, 5]:
            return await self._send_request_and_wait(conn, Ready4SkyCommand.RUN_CURRENT_MODE, timeout=1.0, require_success=True)

        return False

    async def sendOff(self, conn):
        return await self._send_request_and_wait(conn, Ready4SkyCommand.STOP_CURRENT_MODE, timeout=1.0, require_success=True)

    async def sendSyncDateTime(self, conn):
        if self._type in [0, 3, 4, 5]:
            return True

        if self._type in [1, 2]:
            now = self.decToHex(int(time.time()))
            offset = self.decToHex(time.timezone * -1)

            return await conn.sendRequest(Ready4SkyCommand.SET_TIME, now + offset + '0000')

        return False

    async def sendStat(self, conn):
        if await self._send_request_and_wait(conn, Ready4SkyCommand.GET_STATISTICS_WATT, '00', timeout=1.0):
            if await self._send_request_and_wait(conn, Ready4SkyCommand.GET_STARTS_COUNT, '00', timeout=1.0):
                return True
        return False

    def responseStat(self, arrHex):
        if arrHex[2] == '47':  # state watt
            self._Watts = self.hexToDec(str(arrHex[9] + arrHex[10] + arrHex[11]))  # in Watts
            self._alltime = round(self._Watts / 2200, 1)  # in hours
        elif arrHex[2] == '50':  # state time
            self._times = self.hexToDec(str(arrHex[6] + arrHex[7]))
        self._last_push_ts = time.monotonic()
        self._push_state_update()

    async def sendStatus(self, conn):
        if await self._send_request_and_wait(conn, Ready4SkyCommand.GET_STATUS_MODE, timeout=1.0):
            return True

        return False

    async def _send_settled_status(self, conn, delay: float = 0.8) -> bool:
        """Request status twice to catch delayed state transitions on device side."""
        if not await self.sendStatus(conn):
            return False

        await asyncio.sleep(delay)

        return await self.sendStatus(conn)

    def responseStatus(self, arrHex):
        if self._type == 0:
            self._temp = self.hexToDec(arrHex[13])
            self._status = arrHex[11]
            self._mode = arrHex[3]

            if arrHex[5] != '00':
                self._tgtemp = self.hexToDec(arrHex[5])

        elif self._type in [1, 2]:
            self._temp = self.hexToDec(arrHex[8])
            self._status = arrHex[11]
            self._mode = arrHex[3]

            if arrHex[5] != '00':
                self._tgtemp = self.hexToDec(arrHex[5])

            self._conf_sound_on = self.hexToDec(arrHex[7]) == 1

        elif self._type == 3:
            self._status = arrHex[11]
            self._mode = arrHex[5]
            self._ion = arrHex[14]
        elif self._type == 4:
            self._status = arrHex[11]
            self._mode = arrHex[3]
        elif self._type == 5:
            self._prog = arrHex[3]
            self._sprog = arrHex[4]
            self._temp = self.hexToDec(arrHex[5])

            if arrHex[5] != '00':
                self._tgtemp = self.hexToDec(arrHex[5])

            self._ph = self.hexToDec(arrHex[6])
            self._pm = self.hexToDec(arrHex[7])
            self._th = self.hexToDec(arrHex[8])
            self._tm = self.hexToDec(arrHex[9])
            self._mode = arrHex[10]
            self._status = arrHex[11]

        self._time_upd = time.strftime("%H:%M")
        self._last_push_ts = time.monotonic()
        # Reflect real device mode immediately in disconnect policy.
        self._conn.set_disconnect_delay(300)
        self._push_state_update()

    async def sendConfEnableSound(self, conn, on: bool):
        if await conn.sendRequest(Ready4SkyCommand.SET_SOUND, self.decToHex(int(on))):
            return True
        return False

    async def setConfEnableSound(self, on: bool):
        if self._is_duplicate_command(f"sound:{int(on)}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendConfEnableSound(conn, on):
                        await self._send_settled_status(conn, delay=0.3)
                        return True
            except Exception as ex:
                _LOGGER.debug("setConfEnableSound failed for %s: %s", self._mac, ex)

        return False

    async def setUseBacklight(self, on: bool):
        if self._is_duplicate_command(f"backlight:{int(on)}"):
            return True
        self._use_backlight = bool(on)
        self._push_state_update()

        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendUseBackLight(conn):
                        await self._send_settled_status(conn, delay=0.3)
                        return True
            except Exception as ex:
                _LOGGER.debug("setUseBacklight failed for %s: %s", self._mac, ex)

        return False

    # 00 - boil
    # 01 - heat
    # 03 - backlight (boil by default)
    # temp - temp or rgb in HEX
    async def sendMode(self, conn, mode: str, temp: str = '00'):
        if self._type in [3, 4, 5]:
            return True

        if self._type == 0:
            str2b = mode + '00' + temp + '00'
        elif self._type in [1, 2]:
            str2b = mode + '00' + temp + '00000000000000000000800000'
        else:
            return True

        return await self._send_request_and_wait(
            conn,
            Ready4SkyCommand.SET_STATUS_MODE,
            str2b,
            timeout=1.0,
            require_success=True,
        )

    async def sendModeCook(self, conn, prog, sprog, temp, hours, minutes, dhours, dminutes, heat):
        if self._type == 5:
            str2b = prog + sprog + temp + hours + minutes + dhours + dminutes + heat
            return await self._send_request_and_wait(
                conn,
                Ready4SkyCommand.SET_STATUS_MODE,
                str2b,
                timeout=1.0,
                require_success=True,
            )
        else:
            return True

    @staticmethod
    def _responseAck(_arr_hex: list[str]) -> None:
        """Ack-only callback used to complete cmd+iter waiters for control commands."""

    async def sendTimerCook(self, conn, hours, minutes):
        if self._type == 5:
            return await conn.sendRequest(Ready4SkyCommand.SET_TIME_COOKER, hours + minutes)
        else:
            return True

    async def sendTemperature(self, conn, temp: str):  # temp in HEX or speed 00-06
        if self._type in [1, 2, 3, 5]:
            return await conn.sendRequest(Ready4SkyCommand.SET_TEMPERATURE, temp)
        else:
            return True

    async def sendIonCmd(self, conn, onoff):  # 00-off 01-on
        if self._type == 3:
            return await conn.sendRequest(Ready4SkyCommand.SET_IONIZATION, onoff)

        return True

    async def sendAfterSpeed(self, conn):
        if self._type == 3:
            return await conn.makeRequest('55' + self.getHexNextIter() + '0900aa')

        return True

    async def sendUseBackLight(self, conn):
        if self._type in [0, 3, 4, 5]:
            return True

        onoff = "00"
        if self._type in [1, 2]:
            if self._use_backlight:
                onoff = "01"

            return await conn.sendRequest(Ready4SkyCommand.SET_BACKLIGHT_MODE, 'c8c8' + onoff)

        return False

    async def sendSetLights(self, conn, boilOrLight='01', rgb1='0000ff'):  # 00 - boil light  01 - backlight
        if self._type in [0, 3, 4, 5]:
            return True

        if self._type in [1, 2]:
            scale_light = ['28', '46', '64'] if boilOrLight == "00" else ['00', '32', '64'];
            bright = self.decToHex(self._nightlight_brightness)
            rgb2 = self.rgbToHex(self._rgb2)

            return await conn.sendRequest(
                Ready4SkyCommand.SET_COLOR,
                boilOrLight
                + scale_light[0] + bright + rgb1
                + scale_light[1] + bright + rgb1
                + scale_light[2] + bright + rgb2
            )

        return False

    async def startNightColor(self):
        if self._is_duplicate_command("night_color"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if self._status == STATUS_ON and self._mode != MODE_LIGHT:
                        await self.sendOff(conn)

                    if await self.sendSetLights(conn, '01', self.rgbToHex(self._rgb1)):
                        if await self.sendMode(conn, MODE_LIGHT):
                            if await self.sendOn(conn):
                                if await self._send_settled_status(conn):
                                    return True
            except Exception as ex:
                _LOGGER.debug("startNightColor failed for %s: %s", self._mac, ex)

        return False

    async def modeOn(self, mode=MODE_BOIL, temp: int = 0):
        intent_seq = await self._set_power_intent("on")
        if self._is_duplicate_command(f"mode_on:{mode}:{temp}"):
            return True
        async with self._user_operation():
            if await self._is_stale_power_intent("on", intent_seq):
                _LOGGER.debug("Skipped stale modeOn for %s (newer power intent exists)", self._mac)
                return True
            try:
                async with self._conn as conn:
                    if self._status != STATUS_OFF:
                        await self.sendOff(conn)

                    if await self.sendMode(conn, mode, self.decToHex(temp)):
                        if await self.sendOn(conn) and await self._send_settled_status(conn):
                            return True
            except Exception as ex:
                _LOGGER.debug("modeOn failed for %s: %s", self._mac, ex)

        return False

    async def modeOnCook(self, prog, sprog, temp, hours, minutes, dhours='00', dminutes='00', heat='01'):
        intent_seq = await self._set_power_intent("on")
        if self._is_duplicate_command(f"mode_on_cook:{prog}:{sprog}:{temp}:{hours}:{minutes}:{dhours}:{dminutes}:{heat}"):
            return True
        async with self._user_operation():
            if await self._is_stale_power_intent("on", intent_seq):
                _LOGGER.debug("Skipped stale modeOnCook for %s (newer power intent exists)", self._mac)
                return True
            try:
                async with self._conn as conn:
                    if self._status != STATUS_OFF:
                        await self.sendOff(conn)

                    if await self.sendModeCook(conn, prog, sprog, temp, hours, minutes, dhours, dminutes, heat):
                        if await self.sendOn(conn):
                            if await self._send_settled_status(conn):
                                return True
            except Exception as ex:
                _LOGGER.debug("modeOnCook failed for %s: %s", self._mac, ex)

        return False

    async def modeTempCook(self, temp):
        if self._is_duplicate_command(f"mode_temp_cook:{temp}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTemperature(conn, temp) and await self._send_settled_status(conn):
                        return True
            except Exception as ex:
                _LOGGER.debug("modeTempCook failed for %s: %s", self._mac, ex)

        return False

    async def modeFan(self, speed):
        if self._is_duplicate_command(f"mode_fan:{speed}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTemperature(conn, speed):
                        if await self.sendAfterSpeed(conn):
                            if self._status == STATUS_OFF:
                                await self.sendOn(conn)
                            if await self._send_settled_status(conn):
                                return True
            except Exception as ex:
                _LOGGER.debug("modeFan failed for %s: %s", self._mac, ex)

        return False

    async def modeIon(self, onoff):
        if self._is_duplicate_command(f"mode_ion:{onoff}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendIonCmd(conn, onoff):
                        if await self._send_settled_status(conn):
                            return True
            except Exception as ex:
                _LOGGER.debug("modeIon failed for %s: %s", self._mac, ex)

        return False

    async def modeTimeCook(self, hours, minutes):
        if self._is_duplicate_command(f"mode_time_cook:{hours}:{minutes}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTimerCook(conn, hours, minutes) and await self._send_settled_status(conn):
                        return True
            except Exception as ex:
                _LOGGER.debug("modeTimeCook failed for %s: %s", self._mac, ex)

        return False

    async def modeOff(self):
        intent_seq = await self._set_power_intent("off")
        if self._is_duplicate_command("mode_off"):
            return True
        async with self._user_operation():
            if await self._is_stale_power_intent("off", intent_seq):
                _LOGGER.debug("Skipped stale modeOff for %s (newer power intent exists)", self._mac)
                return True
            try:
                async with self._conn as conn:
                    if await self.sendOff(conn):
                        if await self._send_settled_status(conn):
                            return True
            except Exception as ex:
                _LOGGER.debug("modeOff failed for %s: %s", self._mac, ex)

        return False

    async def setTemperatureHeat(self, temp: int = CONF_MIN_TEMP):
        temp = CONF_MIN_TEMP if temp < CONF_MIN_TEMP else temp
        temp = CONF_MAX_TEMP if temp > CONF_MAX_TEMP else temp
        self._tgtemp = temp

        if self._is_duplicate_command(f"set_temp_heat:{temp}"):
            return True
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTemperature(conn, self.decToHex(temp)):
                        return True
            except Exception as ex:
                _LOGGER.debug("setTemperatureHeat failed for %s: %s", self._mac, ex)

        return False

    async def update(self, now, **kwargs) -> bool:
        if self._op_lock.locked() or await self._has_user_waiting():
            _LOGGER.debug("Poll skipped for %s: user operation in progress", self._mac)
            return True

        visible_now = self._conn._get_ble_device() is not None
        if not visible_now:
            self._offline_streak += 1
            if self._offline_streak >= 3 and not self.is_active:
                self._cold_mode = True
                if self._available:
                    self._available = False
                    self._push_state_update()
                _LOGGER.debug("Poll cold-mode skip for %s: offline streak=%s", self._mac, self._offline_streak)
                return True
        else:
            self._offline_streak = 0
            self._cold_mode = False

        if time.monotonic() - self._last_push_ts < 1.5 and not self._is_in_global_command_window():
            _LOGGER.debug("Poll skipped for %s: recent push-notification update", self._mac)
            return True

        session_acquired = False
        poll_started = time.monotonic()
        try:
            # Poll must not block user commands on connect phase.
            conn = await self._conn.acquire_session(blocking=False)
            session_acquired = True
            async with self._op_lock:
                # Keep active devices connected; idle ones may disconnect by delay timer.
                self._conn.set_disconnect_delay(300)
                status_ok = await self.sendStatus(conn)
                if status_ok:
                    # Give priority to user command burst; keep poll short.
                    if await self._has_user_waiting() or self._is_in_global_command_window():
                        _LOGGER.debug("Poll shortened for %s due to pending user operation", self._mac)
                        self._available = True
                        return True
                    now_ts = time.monotonic()
                    if now_ts - self._last_sync_ts >= 600:
                        await self.sendSyncDateTime(conn)
                        self._last_sync_ts = now_ts
                    # Statistics are slow and not required every poll cycle.
                    stat_interval = 30.0 if self.is_active else 120.0
                    if now_ts - self._last_stat_ts >= stat_interval:
                        await self.sendStat(conn)
                        self._last_stat_ts = now_ts
                    self._update_counter += 1
                    self._available = True
                    self._conn.set_disconnect_delay(300)
                    _LOGGER.debug(
                        "Poll success for %s in %.3fs (active=%s, counter=%s)",
                        self._mac,
                        time.monotonic() - poll_started,
                        self.is_active,
                        self._update_counter,
                    )
                    return True
        except BusyConnectionError:
            # Another device is in connect/transaction phase; skip this poll cycle.
            _LOGGER.debug("Poll skipped for %s: BLE connect slot busy/backoff", self._mac)
            return True
        except BleakError as ex:
            self._available = False
            self._push_state_update()
            _LOGGER.debug("Poll BLE error for %s: %s", self._mac, ex)
            return False
        except Exception:
            self._available = False
            self._push_state_update()
            _LOGGER.exception("Poll failed for %s", self._mac)
        finally:
            if session_acquired:
                await self._conn.release_session()

        return False

    async def firstConnect(self):
        _LOGGER.debug('FIRST CONNECT')

        initialized = False
        async with self._op_lock:
            async with self._conn as conn:
                if await self.sendUseBackLight(conn):
                    if await self.sendGetVersion(conn):
                        status_ok = await self.sendSyncDateTime(conn) and await self.sendStatus(conn)
                        if status_ok:
                            self._update_counter += 1
                            self._available = True
                            # Keep first connect short; statistics will be fetched by regular poll.
                            self._last_stat_ts = 0.0
                            self._push_state_update()
                            initialized = True

        if initialized:
            # Release radio immediately after startup init so next device can init
            # without forced peer disconnect/handoff races.
            await self._conn.disconnect()
            # BlueZ/AcquireNotify teardown is slightly asynchronous; a short settle delay
            # avoids immediate reconnect races on the next device startup init.
            await asyncio.sleep(0.2)
            return True

        self._available = False
        self._push_state_update()

        return False
