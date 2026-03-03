#!/usr/local/bin/python3
# coding: utf-8

from dataclasses import dataclass
import logging
import secrets

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .core.const import CONF_USE_BACKLIGHT, DEFAULT_SCAN_INTERVAL, DEFAULT_USE_BACKLIGHT, DOMAIN, SUPPORTED_DOMAINS
from .core.coordinator import Ready4SkyCoordinator
from .core.device import Ready4SkyDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class Ready4SkyRuntimeData:
    device: Ready4SkyDevice
    coordinator: Ready4SkyCoordinator


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    config = config_entry.data
    mac = str(config.get(CONF_MAC)).upper()
    name = config.get(CONF_NAME) or config_entry.title
    password = config.get(CONF_PASSWORD) or secrets.token_hex(8)
    scan_interval = DEFAULT_SCAN_INTERVAL
    backlight = config.get(CONF_USE_BACKLIGHT, DEFAULT_USE_BACKLIGHT)

    kettler = Ready4SkyDevice(hass, mac, password, backlight, name)
    await kettler.setNameAndType()

    first_connect_ok = False
    try:
        first_connect_ok = await kettler.firstConnect()
    except BaseException:
        # Device can be offline during HA startup; keep integration loaded
        # and let coordinator retries bring it online later.
        _LOGGER.debug("Initial connect to %s failed, continuing in unavailable state", mac)

    coordinator = Ready4SkyCoordinator(hass, kettler, scan_interval)
    coordinator.async_set_updated_data(kettler.export_state())
    if not first_connect_ok:
        hass.async_create_task(coordinator.async_refresh())

    config_entry.runtime_data = Ready4SkyRuntimeData(
        device=kettler,
        coordinator=coordinator,
    )

    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        manufacturer="Redmond",
        model=kettler._name,
        name=kettler._name,
        sw_version=kettler._firmware_ver,
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, SUPPORTED_DOMAINS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_DOMAINS)
    runtime_data: Ready4SkyRuntimeData | None = entry.runtime_data
    if runtime_data is not None:
        runtime_data.coordinator.unregister()
        await runtime_data.device.disconnect()
    return unload_ok
