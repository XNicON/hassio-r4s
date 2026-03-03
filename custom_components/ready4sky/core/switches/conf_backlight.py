from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.entity import EntityCategory

from ..entity import Ready4SkyCoordinatorEntity


class Ready4SkyBacklightSwitch(Ready4SkyCoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = SwitchEntityDescription(
            key="conf_use_backlight",
            name=f"{self._device._name} Standby backlight",
            icon="mdi:lightbulb-night",
            device_class=SwitchDeviceClass.SWITCH,
            entity_category=EntityCategory.CONFIG,
        )
        self._attr_unique_id = self._build_unique_id("switch", self.entity_description.key)

    @property
    def is_on(self):
        return bool(self.coordinator.data.get("use_backlight", True))

    async def async_turn_on(self, **kwargs):
        self._optimistic_update(use_backlight=True)
        await self._device.setUseBacklight(True)

    async def async_turn_off(self, **kwargs):
        self._optimistic_update(use_backlight=False)
        await self._device.setUseBacklight(False)
