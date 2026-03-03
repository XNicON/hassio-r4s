#!/usr/local/bin/python3
# coding: utf-8

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import (
    Ready4SkyRuntimeData
)
from .core.water_heaters.cooker import Ready4SkyCooker
from .core.water_heaters.kettle import Ready4SkyKettle


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data: Ready4SkyRuntimeData = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    kettle = coordinator.device

    if kettle._type in [0, 1, 2]:
        async_add_entities([Ready4SkyKettle(coordinator)])

    elif kettle._type == 5:
        async_add_entities([Ready4SkyCooker(coordinator)])

        platform = entity_platform.current_platform.get()
        platform.async_register_entity_service(
            "set_timer",
            {
                vol.Required("hours"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59))
            },
            "async_set_timer"
        )

        platform.async_register_entity_service(
            "set_manual_program",
            {
                vol.Required("prog"): vol.All(vol.Coerce(int), vol.Range(min=0, max=12)),
                vol.Required("subprog"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
                vol.Required("temp"): vol.All(vol.Coerce(int), vol.Range(min=30, max=180)),
                vol.Required("hours"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Required("dhours"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required("dminutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Required("heat"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1))
            },
            "async_set_manual_program"
        )
