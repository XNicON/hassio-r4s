from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription

from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkySwitchIonization(Ready4SkyCoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SwitchEntityDescription(
            key="ionization_on",
            name=f"{self._device._name} Enable Ionization",
            icon="mdi:flash",
            device_class=SwitchDeviceClass.SWITCH,
        )
        self._attr_unique_id = self._build_unique_id("switch", self.entity_description.key)

    @property
    def is_on(self):
        return self.coordinator.data.get("ionization") == '01'

    async def async_turn_on(self, **kwargs):
        self._optimistic_update(ionization='01')
        await self._device.modeIon('01')

    async def async_turn_off(self, **kwargs):
        self._optimistic_update(ionization='00')
        await self._device.modeIon('00')
