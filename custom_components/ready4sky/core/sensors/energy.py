from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import UnitOfEnergy

from ..const import ATTR_TIMES, ATTR_WORK_ALLTIME
from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkyEnergySensor(Ready4SkyCoordinatorEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SensorEntityDescription(
            key="energy",
            name=f"{self._device._name} Energy",
            icon="mdi:lightning-bolt",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        )

        self._attr_unique_id = self._build_unique_id("sensor", self.entity_description.key)

    @property
    def native_value(self):
        return self.coordinator.data.get("energy_wh", 0)

    @property
    def extra_state_attributes(self):
        return {
            ATTR_TIMES: self.coordinator.data.get("times_started", 0),
            ATTR_WORK_ALLTIME: self.coordinator.data.get("work_hours", 0),
        }
