from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

class Ready4SkyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    _poll_semaphore = asyncio.Semaphore(1)
    _active_devices: set[str] = set()

    def __init__(self, hass: HomeAssistant, device, scan_interval: int) -> None:
        self._base_interval = timedelta(seconds=scan_interval)
        self._active_interval = timedelta(seconds=min(scan_interval, 3))
        super().__init__(
            hass,
            logger=device.logger,
            name=f"ready4sky_{device._mac}",
            update_interval=self._base_interval,
        )
        self.device = device
        self._lock = asyncio.Lock()
        self.device.set_state_callback(self._handle_device_push)

    def _apply_dynamic_interval(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        available = bool(data.get("available", True))
        # Active modes should be polled faster because devices don't push temp continuously.
        active_statuses = {"01", "02", "04", "05"}
        was_active_count = len(self._active_devices)
        if available and status in active_statuses:
            self._active_devices.add(self.device._mac)
        else:
            self._active_devices.discard(self.device._mac)

        self.update_interval = self._active_interval if self._active_devices else self._base_interval
        if len(self._active_devices) != was_active_count:
            _LOGGER.debug(
                "Active device set changed: %s (interval=%ss)",
                sorted(self._active_devices),
                int(self.update_interval.total_seconds()),
            )

    def _publish_push_update(self, data: dict[str, Any]) -> None:
        self._apply_dynamic_interval(data)
        self.async_set_updated_data(data)

    def _handle_device_push(self, data: dict[str, Any]) -> None:
        self.hass.loop.call_soon_threadsafe(self._publish_push_update, data)

    def unregister(self) -> None:
        self._active_devices.discard(self.device._mac)
        _LOGGER.debug("Coordinator unregistered for %s", self.device._mac)

    async def _async_update_data(self) -> dict[str, Any]:
        async with self._lock:
            async with self._poll_semaphore:
                _LOGGER.debug("Coordinator poll start for %s", self.device._mac)
                await self.device.update(None)
                data = self.device.export_state()
                self._apply_dynamic_interval(data)
                _LOGGER.debug("Coordinator poll done for %s", self.device._mac)
                return data
