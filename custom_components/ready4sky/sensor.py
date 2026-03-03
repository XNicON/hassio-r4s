#!/usr/local/bin/python3
# coding: utf-8

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Ready4SkyRuntimeData
from .core.sensors.energy import Ready4SkyEnergySensor
from .core.sensors.status import Ready4SkySensor


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data: Ready4SkyRuntimeData = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    kettle = coordinator.device

    if kettle._type in [0, 1, 2, 3, 4, 5]:
        async_add_entities([
            Ready4SkySensor(coordinator),
            Ready4SkyEnergySensor(coordinator)
        ])
