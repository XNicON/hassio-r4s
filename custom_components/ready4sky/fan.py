#!/usr/local/bin/python3
# coding: utf-8

from homeassistant.components.fan import FanEntity, FanEntityDescription, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Ready4SkyRuntimeData
from .core.const import MODE_BOIL, STATUS_OFF, STATUS_ON
from .core.entity import Ready4SkyCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data: Ready4SkyRuntimeData = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    if coordinator.device._type == 3:
        async_add_entities([Ready4SkyFan(coordinator)])


class Ready4SkyFan(Ready4SkyCoordinatorEntity, FanEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = FanEntityDescription(
            key="fan_on",
            name=f"{self._device._name} Fan",
            icon="mdi:fan",
        )
        self._attr_unique_id = self._build_unique_id("fan", self.entity_description.key)

    @property
    def is_on(self):
        return self.coordinator.data.get("status") == STATUS_ON

    @property
    def speed(self):
        mode = self.coordinator.data.get("mode", MODE_BOIL)
        return '01' if mode == MODE_BOIL else mode

    @property
    def speed_list(self):
        return ['01', '02', '03', '04', '05', '06']

    async def async_set_speed(self, speed: str) -> None:
        if speed == '00':
            self._optimistic_update(status=STATUS_OFF)
            await self._device.modeOff()
        else:
            self._optimistic_update(status=STATUS_ON, mode=speed)
            await self._device.modeFan(speed)

    async def async_turn_on(self, speed: str = None, percentage: int = None, preset_mode: str = None, **kwargs) -> None:
        await self.async_set_speed(speed or '01')

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_update(status=STATUS_OFF)
        await self._device.modeOff()

    @property
    def supported_features(self) -> int:
        return FanEntityFeature.SET_SPEED
