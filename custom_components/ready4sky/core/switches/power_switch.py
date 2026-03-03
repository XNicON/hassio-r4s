from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription

from ..const import MODE_BOIL, STATUS_OFF, STATUS_ON
from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkyPowerSwitch(Ready4SkyCoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SwitchEntityDescription(
            key="power_on",
            name=f"{self._device._name} Turn power"
        )
        self._attr_unique_id = self._build_unique_id("switch", self.entity_description.key)

    @property
    def is_on(self):
        return (
            self.coordinator.data.get("status") == STATUS_ON
            and self.coordinator.data.get("mode") == MODE_BOIL
        )

    async def async_turn_on(self, **kwargs):
        self._optimistic_update(status=STATUS_ON, mode=MODE_BOIL)
        await self._device.modeOn()

    async def async_turn_off(self, **kwargs):
        self._optimistic_update(status=STATUS_OFF)
        await self._device.modeOff()
