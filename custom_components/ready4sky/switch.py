#!/usr/local/bin/python3
# coding: utf-8

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core.switches.conf_backlight import Ready4SkyBacklightSwitch
from .core.switches.conf_sound import Ready4SkyConfSwitchSound
from .core.switches.humidifier_ionization import Ready4SkySwitchIonization
from .core.switches.power_switch import Ready4SkyPowerSwitch
from . import Ready4SkyRuntimeData


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data: Ready4SkyRuntimeData = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    kettle = coordinator.device

    if kettle._type in [1, 2]:
        async_add_entities([
            Ready4SkyConfSwitchSound(coordinator),
            Ready4SkyBacklightSwitch(coordinator),
        ])
    elif kettle._type == 3:
        async_add_entities([
            Ready4SkySwitchIonization(coordinator)
        ])
    elif kettle._type == 4:
        async_add_entities([
            Ready4SkyPowerSwitch(coordinator)
        ])
