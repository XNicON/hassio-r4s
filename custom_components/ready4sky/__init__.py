#!/usr/local/bin/python3
# coding: utf-8

import asyncio
import logging
import time
from datetime import (timedelta)
from enum import Enum

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .btle import BTLEConnection

DOMAIN = "ready4sky"
SUPPORTED_DOMAINS = [
    "water_heater",
    "sensor",
    "light",
    "switch",
    "fan"
]
SIGNAL_UPDATE_DATA = 'ready4skyupdate'

CONF_USE_BACKLIGHT = 'use_backlight'

CONF_MIN_TEMP = 35
CONF_MAX_TEMP = 90

STATUS_OFF = '00'
STATUS_ON = '02'

COOKER_STATUS_PROGRAM = '01'
COOKER_STATUS_KEEP_WARM = '04'
COOKER_STATUS_DELAYED_START = '05'

MODE_BOIL = '00'
MODE_KEEP_WARM = '01'
MODE_LIGHT = '03'

ATTR_WORK_ALLTIME = 'Working time (h)'
ATTR_TIMES = 'Number starts'
ATTR_SYNC = 'Last sync'
ATTR_TIMER_SET = 'Timer set'
ATTR_TIMER_CURR = 'Timer current'

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    config = config_entry.data
    mac = str(config.get(CONF_MAC)).upper()
    password = config.get(CONF_PASSWORD)
    scan_delta = timedelta(seconds=config.get(CONF_SCAN_INTERVAL))
    backlight = config.get(CONF_USE_BACKLIGHT)

    kettler = RedmondKettle(hass, mac, password, backlight)
    await kettler.setNameAndType()

    try:
        await kettler.firstConnect()
    except BaseException as ex:
        _LOGGER.error("Connect to %s failed", mac)
        _LOGGER.exception(ex)
        return False

    hass.data[DOMAIN][config_entry.entry_id] = kettler

    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={
            (DOMAIN, config_entry.unique_id)
        },
        connections={
            (dr.CONNECTION_NETWORK_MAC, mac)
        },
        manufacturer="Redmond",
        model=kettler._name,
        name=kettler._name,
        sw_version=kettler._firmware_ver
    )

    async_track_time_interval(hass, hass.data[DOMAIN][config_entry.entry_id].update, scan_delta)

    await hass.config_entries.async_forward_entry_setups(config_entry, SUPPORTED_DOMAINS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_DOMAINS)
    try:
        hass.data[DOMAIN].pop(entry.entry_id)
    except ValueError:
        pass
    return unload_ok


class RedmondCommand(Enum):
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


class RedmondKettle:
    def __init__(self, hass, addr, key, backlight):
        self.hass = hass
        self._type = None
        self._name = None
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
        self._conn = BTLEConnection(self.hass, self._mac, self._key)
        self._available = False
        self.initCallbacks()

    async def setNameAndType(self):
        await self._conn.setNameAndType()
        self._type = self._conn._type
        self._name = self._conn._name
        self._available = self._conn._available

    def initCallbacks(self):
        self._conn.setConnectAfter(self.sendAuth)
        self._conn.setCallback(RedmondCommand.AUTH, self.responseAuth)
        self._conn.setCallback(RedmondCommand.VERSION, self.responseGetVersion)
        self._conn.setCallback(RedmondCommand.GET_STATUS_MODE, self.responseStatus)
        self._conn.setCallback(RedmondCommand.GET_STATISTICS_WATT, self.responseStat)
        self._conn.setCallback(RedmondCommand.GET_STARTS_COUNT, self.responseStat)

    def hexToRgb(self, hexa: str):
        return tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))

    def rgbToHex(self, rgb):
        return '%02x%02x%02x' % rgb

    @staticmethod
    def hexToDec(hexChr: str) -> int:
        return BTLEConnection.hexToDec(hexChr)

    @staticmethod
    def decToHex(num: int) -> str:
        return BTLEConnection.decToHex(num)

    def getHexNextIter(self) -> str:
        return self._conn.getHexNextIter()

    async def sendAuth(self, conn):
        self._type = conn._type
        self._name = conn._name

        await conn.sendRequest(RedmondCommand.AUTH, self._key)
        await asyncio.sleep(1.5)

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
        return await conn.sendRequest(RedmondCommand.VERSION)

    def responseGetVersion(self, arrHex):
        self._firmware_ver = str(self.hexToDec(arrHex[3])) + '.' + str(self.hexToDec(arrHex[4]))

    async def sendOn(self, conn):
        if self._type == 0:
            return True

        if self._type in [1, 2, 3, 4, 5]:
            return await conn.sendRequest(RedmondCommand.RUN_CURRENT_MODE)

        return False

    async def sendOff(self, conn):
        return await conn.sendRequest(RedmondCommand.STOP_CURRENT_MODE)

    async def sendSyncDateTime(self, conn):
        if self._type in [0, 3, 4, 5]:
            return True

        if self._type in [1, 2]:
            now = self.decToHex(int(time.time()))
            offset = self.decToHex(time.timezone * -1)

            return await conn.sendRequest(RedmondCommand.SET_TIME, now + offset + '0000')

        return False

    async def sendStat(self, conn):
        if await conn.sendRequest(RedmondCommand.GET_STATISTICS_WATT, '00'):
            if await conn.sendRequest(RedmondCommand.GET_STARTS_COUNT, '00'):
                return True
        return False

    def responseStat(self, arrHex):
        if arrHex[2] == '47':  # state watt
            self._Watts = self.hexToDec(str(arrHex[9] + arrHex[10] + arrHex[11]))  # in Watts
            self._alltime = round(self._Watts / 2200, 1)  # in hours
        elif arrHex[2] == '50':  # state time
            self._times = self.hexToDec(str(arrHex[6] + arrHex[7]))

    async def sendStatus(self, conn):
        if await conn.sendRequest(RedmondCommand.GET_STATUS_MODE):
            return True

        return False

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
        async_dispatcher_send(self.hass, SIGNAL_UPDATE_DATA)

    async def sendConfEnableSound(self, conn, on: bool):
        if await conn.sendRequest(RedmondCommand.SET_SOUND, self.decToHex(int(on))):
            return True
        return False

    async def setConfEnableSound(self, on: bool):
        try:
            async with self._conn as conn:
                if await self.sendConfEnableSound(conn, on):
                    return True
        except:
            pass

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

        return await conn.sendRequest(RedmondCommand.SET_STATUS_MODE, str2b)

    async def sendModeCook(self, conn, prog, sprog, temp, hours, minutes, dhours, dminutes, heat):
        if self._type == 5:
            str2b = prog + sprog + temp + hours + minutes + dhours + dminutes + heat
            return await conn.sendRequest(RedmondCommand.SET_STATUS_MODE, str2b)
        else:
            return True

    async def sendTimerCook(self, conn, hours, minutes):
        if self._type == 5:
            return await conn.sendRequest(RedmondCommand.SET_TIME_COOKER, hours + minutes)
        else:
            return True

    async def sendTemperature(self, conn, temp: str):  # temp in HEX or speed 00-06
        if self._type in [1, 2, 3, 5]:
            return await conn.sendRequest(RedmondCommand.SET_TEMPERATURE, temp)
        else:
            return True

    async def sendIonCmd(self, conn, onoff):  # 00-off 01-on
        if self._type == 3:
            return await conn.sendRequest(RedmondCommand.SET_IONIZATION, onoff)

        return True

    async def sendAfterSpeed(self, conn):
        if self._type == 3:
            return await conn.makeRequest('55' + self.getHexNextIter() + '0900aa', True)

        return True

    async def sendUseBackLight(self, conn):
        if self._type in [0, 3, 4, 5]:
            return True

        onoff = "00"
        if self._type in [1, 2]:
            if self._use_backlight:
                onoff = "01"

            return await conn.sendRequest(RedmondCommand.SET_BACKLIGHT_MODE, 'c8c8' + onoff)

        return False

    async def sendSetLights(self, conn, boilOrLight='01', rgb1='0000ff'):  # 00 - boil light  01 - backlight
        if self._type in [0, 3, 4, 5]:
            return True

        if self._type in [1, 2]:
            scale_light = ['28', '46', '64'] if boilOrLight == "00" else ['00', '32', '64'];
            bright = self.decToHex(self._nightlight_brightness)
            rgb2 = self.rgbToHex(self._rgb2)

            return await conn.sendRequest(
                RedmondCommand.SET_COLOR,
                boilOrLight
                + scale_light[0] + bright + rgb1
                + scale_light[1] + bright + rgb1
                + scale_light[2] + bright + rgb2
            )

        return False

    async def startNightColor(self):
        try:
            async with self._conn as conn:
                if self._status == STATUS_ON and self._mode != MODE_LIGHT:
                    await self.sendOff(conn)

                if await self.sendSetLights(conn, '01', self.rgbToHex(self._rgb1)):
                    if await self.sendMode(conn, MODE_LIGHT):
                        if await self.sendOn(conn):
                            if await self.sendStatus(conn):
                                return True
        except:
            pass

        return False

    async def modeOn(self, mode=MODE_BOIL, temp: int = 0):
        try:
            async with self._conn as conn:
                if self._status != STATUS_OFF:
                    await self.sendOff(conn)

                if await self.sendMode(conn, mode, self.decToHex(temp)):
                    if await self.sendOn(conn) and await self.sendStatus(conn):
                        return True
        except:
            pass

        return False

    async def modeOnCook(self, prog, sprog, temp, hours, minutes, dhours='00', dminutes='00', heat='01'):
        try:
            async with self._conn as conn:
                if self._status != STATUS_OFF:
                    await self.sendOff(conn)

                if await self.sendModeCook(conn, prog, sprog, temp, hours, minutes, dhours, dminutes, heat):
                    if await self.sendOn(conn):
                        if await self.sendStatus(conn):
                            return True
        except:
            pass

        return False

    async def modeTempCook(self, temp):
        try:
            async with self._conn as conn:
                if await self.sendTemperature(conn, temp) and await self.sendStatus(conn):
                    return True
        except:
            pass

        return False

    async def modeFan(self, speed):
        try:
            async with self._conn as conn:
                if await self.sendTemperature(conn, speed):
                    if await self.sendAfterSpeed(conn):
                        if self._status == STATUS_OFF:
                            await self.sendOn(conn)
                        if await self.sendStatus(conn):
                            return True
        except:
            pass

        return False

    async def modeIon(self, onoff):
        try:
            async with self._conn as conn:
                if await self.sendIonCmd(conn, onoff):
                    if await self.sendStatus(conn):
                        return True
        except:
            pass

        return False

    async def modeTimeCook(self, hours, minutes):
        try:
            async with self._conn as conn:
                if await self.sendTimerCook(conn, hours, minutes) and await self.sendStatus(conn):
                    return True
        except:
            pass

        return False

    async def modeOff(self):
        try:
            async with self._conn as conn:
                if await self.sendOff(conn):
                    if await self.sendStatus(conn):
                        return True
        except:
            pass

        return False

    async def setTemperatureHeat(self, temp: int = CONF_MIN_TEMP):
        temp = CONF_MIN_TEMP if temp < CONF_MIN_TEMP else temp
        temp = CONF_MAX_TEMP if temp > CONF_MAX_TEMP else temp
        self._tgtemp = temp

        try:
            async with self._conn as conn:
                if await self.sendTemperature(conn, self.decToHex(temp)):
                    return True
        except:
            pass

        return False

    async def update(self, now, **kwargs) -> bool:
        try:
            async with self._conn as conn:
                if await self.sendSyncDateTime(conn) and await self.sendStatus(conn) and await self.sendStat(conn):
                    return True
        except:
            pass

        return False

    async def firstConnect(self):
        _LOGGER.debug('FIRST CONNECT')

        async with self._conn as conn:
            if await self.sendUseBackLight(conn):
                if await self.sendGetVersion(conn):
                    if await self.update(1):
                        return True

        self._available = False

        return False
