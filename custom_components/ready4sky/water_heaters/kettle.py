from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    ATTR_TEMPERATURE,
    WaterHeaterEntityDescription
)
from homeassistant.const import (
    STATE_OFF,
    UnitOfTemperature,
    PRECISION_WHOLE
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .. import (
    DOMAIN,
    SIGNAL_UPDATE_DATA,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    MODE_KEEP_WARM,
    MODE_BOIL,
    STATUS_ON
)

STATE_BOIL = 'boil'
STATE_KEEP_WARM = 'keep_warm'


class RedmondKettle(WaterHeaterEntity):
    def __init__(self, kettle):
        self._kettle = kettle
        self.entity_description = WaterHeaterEntityDescription(
            key="kettle",
            name=kettle._name + " Kettle",
            icon="mdi:kettle"
        )

        self._attr_translation_key = 'r4s'

        self._attr_unique_id = f'{DOMAIN}[{kettle._mac}][wheater][{self.entity_description.key}]'
        self._attr_device_info = DeviceInfo(connections={("mac", kettle._mac)})
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_WHOLE

        self._attr_current_temperature = 0
        self._attr_target_temperature = CONF_MIN_TEMP
        self._attr_current_operation = STATE_OFF
        self._attr_min_temp = CONF_MIN_TEMP
        self._attr_max_temp = CONF_MAX_TEMP
        self._attr_operation_list = [
            STATE_OFF,
            STATE_BOIL,
            STATE_KEEP_WARM
        ]
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
        )

    async def async_added_to_hass(self):
        self.update()
        self.async_on_remove(async_dispatcher_connect(self._kettle.hass, SIGNAL_UPDATE_DATA, self.update))

    def update(self):
        self._attr_current_temperature = self._kettle._temp
        self._attr_target_temperature = self._kettle._tgtemp
        self._attr_current_operation = STATE_OFF

        if self._kettle._status == STATUS_ON:
            if self._kettle._mode == MODE_BOIL:
                self._attr_current_operation = STATE_BOIL
            elif self._kettle._mode == MODE_KEEP_WARM:
                self._attr_current_operation = STATE_KEEP_WARM

        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._kettle._available

    @property
    def extra_state_attributes(self):
        return {
            "target_temp_step": 5
        }

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == STATE_OFF:
            await self._kettle.modeOff()
        elif operation_mode == STATE_BOIL:
            await self._kettle.modeOn()
        elif operation_mode == STATE_KEEP_WARM:
            await self._kettle.modeOn(MODE_KEEP_WARM, self._attr_target_temperature)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        newTargetTemperature = int(kwargs.get(ATTR_TEMPERATURE))

        # if setTemperature from other integrations, simply set keep warm
        if (newTargetTemperature - self.target_temperature) == 1:
            return await self.async_set_operation_mode(STATE_KEEP_WARM)

        self._kettle._tgtemp = newTargetTemperature
        self.update()

        if self.state == STATE_KEEP_WARM:
            await self.async_set_operation_mode(STATE_KEEP_WARM)
        elif self.state == STATE_OFF:
            await self._kettle.setTemperatureHeat(self._kettle._tgtemp)

    async def async_turn_on(self):
        await self.async_set_operation_mode(STATE_BOIL)

    async def async_turn_off(self):
        await self.async_set_operation_mode(STATE_OFF)
