#!/usr/local/bin/python3
# coding: utf-8

import asyncio
import binascii
import inspect
import logging
from textwrap import wrap

from bleak import (BleakClient, BleakError)
from homeassistant.components import bluetooth

from .r4sconst import SUPPORTED_DEVICES

_LOGGER = logging.getLogger(__name__)

UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"


class BTLEConnection:
    def __init__(self, hass, mac, key, ):
        self._type = None
        self._name = ''
        self._hass = hass
        self._mac = mac
        self._key = key
        self._iter = 0
        self._callbacks = {}
        self._afterConnectCallback = None
        self._conn = None
        self._device = None
        self._available = False

    async def setNameAndType(self):
        self._device = bluetooth.async_ble_device_from_address(self._hass, self._mac, False)

        if self._device is None:
            _LOGGER.debug('Device "%s" not found on bluetooth network', self._mac)
            return self

        self._name = self._device.name
        self._type = SUPPORTED_DEVICES.get(self._name)

        if self._type is None:
            self._available = False
            _LOGGER.error('Device "%s" not supported. Please report developer or view file r4sconst.py', self._name)
            return self

        self._available = True

        return self

    async def __aenter__(self):
        if self._type is None:
            await self.setNameAndType()
            if self._type is None:
                return self

        for i in range(3):
            isConnected = self._conn is not None and self._conn.is_connected

            _LOGGER.debug('IS CONNECTED: %s', str(isConnected))

            if isConnected:
                break

            try:
                self._conn = BleakClient(self._device or self._mac)

                await self._conn.connect()
                await self._conn.stop_notify(UART_TX_CHAR_UUID)
                await self._conn.start_notify(UART_TX_CHAR_UUID, self.handleNotification)
                await self.connectAfter()
                break
            except BaseException as ex:
                _LOGGER.error('Unable to connect')
                _LOGGER.exception(ex)

                if i < 2:
                    await asyncio.sleep(1 + i)
                else:
                    await self.disconnect()
                    raise ex

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._iter == 255:
            await self.disconnect()

    @staticmethod
    async def getDiscoverDevices(hass):
        devices = await bluetooth.async_get_scanner(hass).discover()

        return {str(device.address): str(device.name) for device in devices}

    async def disconnect(self):
        try:
            await asyncio.sleep(2.0)
            await self._conn.disconnect()

            self._iter = 0
        except BaseException as ex:
            self._available = False
            _LOGGER.error('disconnect failed')
            _LOGGER.exception(ex)

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

        try:
            await self._conn.write_gatt_char(UART_RX_CHAR_UUID, binascii.a2b_hex(bytes(value, 'utf-8')), True)

            return True
        except BleakError as ex:
            _LOGGER.error('not send request %s', inspect.getouterframes(inspect.currentframe(), 2)[1][3])
            _LOGGER.exception(ex)

        return False

    async def sendRequest(self, cmdHex, dataHex=''):
        return await self.makeRequest('55' + self.getHexNextIter() + str(cmdHex) + dataHex + 'aa')

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
