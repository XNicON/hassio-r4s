from homeassistant.components.sensor import SensorEntity, SensorEntityDescription

from ..const import (
    ATTR_SYNC,
    ATTR_TIMER_CURR,
    ATTR_TIMER_SET,
    COOKER_STATUS_DELAYED_START,
    COOKER_STATUS_KEEP_WARM,
    COOKER_STATUS_PROGRAM,
    MODE_BOIL,
    MODE_KEEP_WARM,
    MODE_LIGHT,
    STATUS_ON,
)
from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkySensor(Ready4SkyCoordinatorEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SensorEntityDescription(
            key="status",
            name=f"{self._device._name} Status",
        )
        self._attr_translation_key = 'r4s'
        self._attr_unique_id = self._build_unique_id("sensor", self.entity_description.key)

    @property
    def native_value(self):
        data = self.coordinator.data
        device_type = data.get("type")
        status = data.get("status")
        mode = data.get("mode")

        if device_type == 5:
            if status == COOKER_STATUS_PROGRAM:
                return 'program'
            if status == STATUS_ON:
                return 'on'
            if status == COOKER_STATUS_KEEP_WARM:
                return 'keep_warm'
            if status == COOKER_STATUS_DELAYED_START:
                return 'delayed_start'
            return 'off'

        if status == STATUS_ON:
            if device_type in [3, 4]:
                return 'on'
            if mode == MODE_BOIL:
                return 'boil'
            if mode == MODE_KEEP_WARM:
                return 'keep_warm'
            if mode == MODE_LIGHT:
                return 'light'

        return 'off'

    @property
    def icon(self):
        return 'mdi:toggle-switch' if self.native_value != 'off' else 'mdi:toggle-switch-off'

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        attributes = {
            ATTR_SYNC: str(data.get("last_sync", "00:00"))
        }

        if data.get("type") == 5:
            attributes[ATTR_TIMER_SET] = f"{data.get('program_hours', 0)}:{data.get('program_minutes', 0)}"
            attributes[ATTR_TIMER_CURR] = f"{data.get('timer_hours', 0)}:{data.get('timer_minutes', 0)}"

        return attributes
