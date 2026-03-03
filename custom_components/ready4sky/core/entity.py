from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Ready4SkyCoordinator


class Ready4SkyCoordinatorEntity(CoordinatorEntity[Ready4SkyCoordinator]):
    def __init__(self, coordinator: Ready4SkyCoordinator) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device._mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, self._device._mac)},
            manufacturer="Redmond",
            name=self._device._name,
            model=self._device._name,
            sw_version=self._device._firmware_ver,
        )

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get("available", False))

    def _build_unique_id(self, platform: str, key: str) -> str:
        return f"{self._device._mac}_{platform}_{key}"

    def _optimistic_update(self, **changes) -> None:
        data = dict(self.coordinator.data or self._device.export_state())
        data.update(changes)
        self.coordinator.async_set_updated_data(data)
