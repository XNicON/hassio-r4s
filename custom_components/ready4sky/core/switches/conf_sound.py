from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.entity import EntityCategory

from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkyConfSwitchSound(Ready4SkyCoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SwitchEntityDescription(
            key="conf_sound_on",
            name=f"{self._device._name} Enable sound",
            icon="mdi:volume-high",
            device_class=SwitchDeviceClass.SWITCH,
            entity_category=EntityCategory.CONFIG,
        )

        self._attr_unique_id = self._build_unique_id("switch", self.entity_description.key)

    @property
    def assumed_state(self):
        return False

    @property
    def is_on(self):
        return self.coordinator.data.get("conf_sound_on", False)

    async def async_turn_on(self, **kwargs):
        self._optimistic_update(conf_sound_on=True)
        await self._device.setConfEnableSound(True)

    async def async_turn_off(self, **kwargs):
        self._optimistic_update(conf_sound_on=False)
        await self._device.setConfEnableSound(False)
