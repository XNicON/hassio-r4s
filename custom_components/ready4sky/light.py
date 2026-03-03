#!/usr/local/bin/python3
# coding: utf-8

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Ready4SkyRuntimeData
from .core.const import MODE_LIGHT, STATUS_OFF, STATUS_ON
from .core.entity import Ready4SkyCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data: Ready4SkyRuntimeData = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    kettle = coordinator.device

    if kettle._type in [1, 2]:
        async_add_entities([Ready4SkyNightlight(coordinator)])


class Ready4SkyNightlight(Ready4SkyCoordinatorEntity, LightEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = LightEntityDescription(
            key="nightlight_on",
            name=f"{self._device._name} Nightlight",
            icon="mdi:floor-lamp",
        )
        self._attr_unique_id = self._build_unique_id("light", self.entity_description.key)
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_color_modes = {ColorMode.RGB}

    @property
    def rgb_color(self):
        return self.coordinator.data.get("rgb1", (0, 0, 255))

    @property
    def brightness(self):
        return self.coordinator.data.get("nightlight_brightness", 255)

    @property
    def is_on(self):
        return (
            self.coordinator.data.get("status") == STATUS_ON
            and self.coordinator.data.get("mode") == MODE_LIGHT
        )

    async def async_turn_on(self, **kwargs):
        self._device._nightlight_brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness)
        self._device._rgb1 = kwargs.get(ATTR_RGB_COLOR, self.rgb_color)
        self._optimistic_update(
            status=STATUS_ON,
            mode=MODE_LIGHT,
            nightlight_brightness=self._device._nightlight_brightness,
            rgb1=self._device._rgb1,
        )
        await self._device.startNightColor()

    async def async_turn_off(self, **kwargs):
        self._optimistic_update(status=STATUS_OFF)
        await self._device.modeOff()
