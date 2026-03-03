from typing import Any

from homeassistant.components.water_heater import (
    ATTR_TEMPERATURE,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import PRECISION_WHOLE, STATE_OFF, UnitOfTemperature

from ..const import CONF_MAX_TEMP, CONF_MIN_TEMP, MODE_BOIL, MODE_KEEP_WARM, STATUS_OFF, STATUS_ON
from ..entity import Ready4SkyCoordinatorEntity

STATE_BOIL = 'boil'
STATE_KEEP_WARM = 'keep_warm'


class Ready4SkyKettle(Ready4SkyCoordinatorEntity, WaterHeaterEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = WaterHeaterEntityDescription(
            key="kettle",
            name=f"{self._device._name} Kettle",
            icon="mdi:kettle"
        )

        self._attr_translation_key = 'r4s'
        self._attr_unique_id = self._build_unique_id("water_heater", self.entity_description.key)
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_WHOLE
        self._attr_min_temp = CONF_MIN_TEMP
        self._attr_max_temp = CONF_MAX_TEMP
        self._attr_operation_list = [STATE_OFF, STATE_BOIL, STATE_KEEP_WARM]
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )

    @property
    def current_temperature(self):
        return self.coordinator.data.get("current_temperature")

    @property
    def target_temperature(self):
        return self.coordinator.data.get("target_temperature", CONF_MIN_TEMP)

    @property
    def current_operation(self):
        if self.coordinator.data.get("status") == STATUS_ON:
            mode = self.coordinator.data.get("mode")
            if mode == MODE_BOIL:
                return STATE_BOIL
            if mode == MODE_KEEP_WARM:
                return STATE_KEEP_WARM
        return STATE_OFF

    @property
    def extra_state_attributes(self):
        return {"target_temp_step": 5}

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == STATE_OFF:
            self._optimistic_update(status=STATUS_OFF)
            await self._device.modeOff()
        elif operation_mode == STATE_BOIL:
            self._optimistic_update(status=STATUS_ON, mode=MODE_BOIL)
            await self._device.modeOn()
        elif operation_mode == STATE_KEEP_WARM:
            self._optimistic_update(status=STATUS_ON, mode=MODE_KEEP_WARM)
            await self._device.modeOn(MODE_KEEP_WARM, self.target_temperature)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        new_target_temperature = int(kwargs.get(ATTR_TEMPERATURE))
        self._optimistic_update(target_temperature=new_target_temperature)

        if (new_target_temperature - self.target_temperature) == 1:
            await self.async_set_operation_mode(STATE_KEEP_WARM)
            return

        self._device._tgtemp = new_target_temperature

        if self.current_operation == STATE_KEEP_WARM:
            await self.async_set_operation_mode(STATE_KEEP_WARM)
        elif self.current_operation == STATE_OFF:
            await self._device.setTemperatureHeat(self._device._tgtemp)

    async def async_turn_on(self):
        await self.async_set_operation_mode(STATE_BOIL)

    async def async_turn_off(self):
        await self.async_set_operation_mode(STATE_OFF)
