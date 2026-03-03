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
    def __init__(self, hass, addr, key, backlight, name=None):
        self.hass = hass
        self._type = None
        self._name = name
        self._mac = addr
        self._key = key
        self._use_backlight = backlight
        self._tgtemp = CONF_MIN_TEMP
        self._temp = 0
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
        self._auth_event = asyncio.Event()
        self._conn = BLEReady4SkyClient(self.hass, self._mac, self._key, self._name)
        self._available = False
        self._state_callback: Callable[[dict], None] | None = None
        self._op_lock = asyncio.Lock()
        self._user_waiting = 0
        self._user_waiting_lock = asyncio.Lock()
        self._update_counter = 0
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
            "current_temperature": self._temp,
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
        }

    def _push_state_update(self):
        if self._state_callback is not None:
            self._state_callback(self.export_state())

    async def _has_user_waiting(self) -> bool:
        async with self._user_waiting_lock:
            return self._user_waiting > 0

    @asynccontextmanager
    async def _user_operation(self):
        wait_started = time.monotonic()
        async with self._user_waiting_lock:
            self._user_waiting += 1
            queue_len = self._user_waiting
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
        self._conn.setCallback(Ready4SkyCommand.AUTH, self.responseAuth)
        self._conn.setCallback(Ready4SkyCommand.VERSION, self.responseGetVersion)
        self._conn.setCallback(Ready4SkyCommand.GET_STATUS_MODE, self.responseStatus)
        self._conn.setCallback(Ready4SkyCommand.GET_STATISTICS_WATT, self.responseStat)
        self._conn.setCallback(Ready4SkyCommand.GET_STARTS_COUNT, self.responseStat)

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
        self._auth_event.clear()
        await conn.sendRequest(Ready4SkyCommand.AUTH, self._key)
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=3.0)
        except asyncio.TimeoutError as ex:
            raise Exception('error auth timeout') from ex

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

        self._auth_event.set()

        return self._auth

    async def sendGetVersion(self, conn):
        return await conn.sendRequest(Ready4SkyCommand.VERSION)

    def responseGetVersion(self, arrHex):
        self._firmware_ver = str(self.hexToDec(arrHex[3])) + '.' + str(self.hexToDec(arrHex[4]))
        self._push_state_update()

    async def sendOn(self, conn):
        if self._type == 0:
            return True

        if self._type in [1, 2, 3, 4, 5]:
            return await conn.sendRequest(Ready4SkyCommand.RUN_CURRENT_MODE)

        return False

    async def sendOff(self, conn):
        return await conn.sendRequest(Ready4SkyCommand.STOP_CURRENT_MODE)

    async def sendSyncDateTime(self, conn):
        if self._type in [0, 3, 4, 5]:
            return True

        if self._type in [1, 2]:
            now = self.decToHex(int(time.time()))
            offset = self.decToHex(time.timezone * -1)

            return await conn.sendRequest(Ready4SkyCommand.SET_TIME, now + offset + '0000')

        return False

    async def sendStat(self, conn):
        if await conn.sendRequest(Ready4SkyCommand.GET_STATISTICS_WATT, '00'):
            if await conn.sendRequest(Ready4SkyCommand.GET_STARTS_COUNT, '00'):
                return True
        return False

    def responseStat(self, arrHex):
        if arrHex[2] == '47':  # state watt
            self._Watts = self.hexToDec(str(arrHex[9] + arrHex[10] + arrHex[11]))  # in Watts
            self._alltime = round(self._Watts / 2200, 1)  # in hours
        elif arrHex[2] == '50':  # state time
            self._times = self.hexToDec(str(arrHex[6] + arrHex[7]))
        self._push_state_update()

    async def sendStatus(self, conn):
        if await conn.sendRequest(Ready4SkyCommand.GET_STATUS_MODE):
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
        # Reflect real device mode immediately in disconnect policy.
        self._conn.set_disconnect_delay(300 if self.is_active else 60)
        self._push_state_update()

    async def sendConfEnableSound(self, conn, on: bool):
        if await conn.sendRequest(Ready4SkyCommand.SET_SOUND, self.decToHex(int(on))):
            return True
        return False

    async def setConfEnableSound(self, on: bool):
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

        return await conn.sendRequest(Ready4SkyCommand.SET_STATUS_MODE, str2b)

    async def sendModeCook(self, conn, prog, sprog, temp, hours, minutes, dhours, dminutes, heat):
        if self._type == 5:
            str2b = prog + sprog + temp + hours + minutes + dhours + dminutes + heat
            return await conn.sendRequest(Ready4SkyCommand.SET_STATUS_MODE, str2b)
        else:
            return True

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
        async with self._user_operation():
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
        async with self._user_operation():
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
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTemperature(conn, temp) and await self._send_settled_status(conn):
                        return True
            except Exception as ex:
                _LOGGER.debug("modeTempCook failed for %s: %s", self._mac, ex)

        return False

    async def modeFan(self, speed):
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
        async with self._user_operation():
            try:
                async with self._conn as conn:
                    if await self.sendTimerCook(conn, hours, minutes) and await self._send_settled_status(conn):
                        return True
            except Exception as ex:
                _LOGGER.debug("modeTimeCook failed for %s: %s", self._mac, ex)

        return False

    async def modeOff(self):
        async with self._user_operation():
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

        session_acquired = False
        poll_started = time.monotonic()
        try:
            # Poll must not block user commands on connect phase.
            conn = await self._conn.acquire_session(blocking=False)
            session_acquired = True
            async with self._op_lock:
                # Keep active devices connected; idle ones may disconnect by delay timer.
                self._conn.set_disconnect_delay(300 if self.is_active else 60)
                status_ok = await self.sendStatus(conn)
                if status_ok:
                    # Give priority to user command burst; keep poll short.
                    if await self._has_user_waiting():
                        _LOGGER.debug("Poll shortened for %s due to pending user operation", self._mac)
                        self._available = True
                        return True
                    if self._update_counter % 30 == 0:
                        await self.sendSyncDateTime(conn)
                    # Statistics are slow and not required every poll cycle.
                    if self._update_counter % 5 == 0:
                        await self.sendStat(conn)
                    self._update_counter += 1
                    self._available = True
                    self._conn.set_disconnect_delay(300 if self.is_active else 60)
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
        except BaseException:
            self._available = False
            self._push_state_update()
            _LOGGER.exception("Poll failed for %s", self._mac)
        finally:
            if session_acquired:
                await self._conn.release_session()

        return False

    async def firstConnect(self):
        _LOGGER.debug('FIRST CONNECT')

        async with self._op_lock:
            async with self._conn as conn:
                if await self.sendUseBackLight(conn):
                    if await self.sendGetVersion(conn):
                        status_ok = await self.sendSyncDateTime(conn) and await self.sendStatus(conn)
                        if status_ok:
                            await self.sendStat(conn)
                            self._update_counter += 1
                            self._available = True
                            self._push_state_update()
                            return True

        self._available = False
        self._push_state_update()

        return False
