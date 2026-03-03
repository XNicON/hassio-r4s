#!/usr/local/bin/python3
# coding: utf-8

import secrets

from homeassistant import config_entries
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from voluptuous import In, Required, Schema

from .core.const import CONF_USE_BACKLIGHT, DEFAULT_SCAN_INTERVAL, DEFAULT_USE_BACKLIGHT, DOMAIN
from .core.ble_client import BLEReady4SkyClient
from .core.r4sconst import SUPPORTED_DEVICES


# @config_entries.HANDLERS.register(DOMAIN)
class Ready4SkyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        self.data = {}
        self._bleDevices = {}

    async def async_step_user(self, user_input={}):
        if user_input:
            return await self.check_valid(user_input)
        return await self.show_form()

    async def async_step_info(self, user_input={}):
        return await self.create_entryS()

    async def show_form(self, user_input={}, errors={}):
        self._bleDevices = await BLEReady4SkyClient.getDiscoverDevices(self.hass)
        bleDevices = self._bleDevices.copy()

        for address, name in bleDevices.items():
            if address.replace(':', '') != bleDevices[address].replace('-', ''):
                bleDevices[address] += ' (' + address + ')'

            bleDevices[address] += ' - Supported' if SUPPORTED_DEVICES.get(name) is not None else ' - Not supported'

        mac = str(user_input.get(CONF_MAC)).upper()
        SCHEMA = Schema({
            Required(CONF_MAC, default=mac): In(bleDevices),
        })

        return self.async_show_form(step_id='user', data_schema=SCHEMA, errors=errors)

    def show_form_info(self):
        return self.async_show_form(step_id='info')

    async def create_entryS(self):
        await self.async_set_unique_id(f'{DOMAIN}[{self.data.get(CONF_MAC)}]')
        return self.async_create_entry(title=self.context["title_placeholders"]['name'], data=self.data)

    async def check_valid(self, user_input):
        mac = user_input.get(CONF_MAC)
        identifier = f'{DOMAIN}[{mac}]'
        if identifier in self._async_current_ids():
            return self.async_abort(reason='already_configured')

        if SUPPORTED_DEVICES.get(self._bleDevices[mac]) is None:
            return await self.show_form(
                user_input=user_input,
                errors={
                    'base': 'device_not_supported'
                }
            )

        self.data = user_input
        self.data[CONF_PASSWORD] = secrets.token_hex(8)
        self.data[CONF_USE_BACKLIGHT] = DEFAULT_USE_BACKLIGHT
        self.data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        self.data[CONF_NAME] = self._bleDevices[mac]
        self.context["title_placeholders"] = {"name": self._bleDevices[mac]}

        return self.show_form_info()
