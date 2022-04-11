"""Ember Mug Custom Integration."""
import asyncio
import logging
from typing import cast

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    CONF_TEMPERATURE_UNIT,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .mug import EmberMug

PLATFORMS = [Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


class MugDataUpdateCoordinator(DataUpdateCoordinator):
    """Shared Data Coordinator for polling mug updates."""

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Init data coordinator and start mug running."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"ember-mug-{config.entry_id}",
            update_interval=None,
        )
        self.mac_address = config.data[CONF_MAC]
        self.name = config.data.get(CONF_NAME, f"Ember Mug {self.mac_address}")
        self.unit_of_measurement = (
            TEMP_FAHRENHEIT
            if "F" in config.data.get(CONF_TEMPERATURE_UNIT)
            else TEMP_CELSIUS
        )

        self.mug = EmberMug(
            self.mac_address,
            self.unit_of_measurement != TEMP_FAHRENHEIT,
            self._sync_callback,
        )
        _LOGGER.info(f"Ember Mug {self.name} Setup")
        # Start loop
        _LOGGER.debug(f"Start running {self.name}")
        self.hass.async_create_task(self._run())
        # Default Data
        self.data = {
            "mug_id": None,
            "serial_number": None,
            "last_read_time": None,
            "sw_version": None,
            "mug_name": "Ember Mug",
            "model": "Ember Mug",
        }

    def _sync_callback(self) -> None:
        """Add a sync callback to execute async update in hass."""
        self.hass.async_create_task(self.async_refresh())

    async def _async_update_data(self):
        """Update the data of the coordinator."""
        data = {
            "mug_id": self.mug.mug_id,
            "serial_number": self.mug.serial_number,
            "last_read_time": dt_util.utcnow(),
            "sw_version": str(self.mug.firmware_info.get("version", "")),
            "mug_name": self.mug.mug_name,
            "model": self.mug.model,
        }
        _LOGGER.debug(f"{data}")
        return data

    @property
    def device_info(self) -> DeviceInfo:
        """Return information about the mug."""
        unique_id = cast(str, self.config_entry.unique_id)
        return DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=self.data["mug_name"],
            model=self.data["model"],
            sw_version=self.data["sw_version"],
            manufacturer="Ember",
        )

    async def _run(self):
        """Start the task loop."""
        try:
            self._loop = True
            _LOGGER.info(f"Starting mug loop {self.mac_address}")
            # Make sure we're disconnected first
            await self.mug.disconnect()
            await self.mug.ensure_connected()
            services = await self.mug.client.get_services()
            debug_services = {
                service.uuid: {
                    "handle": service.handle,
                    "description": service.description,
                    "characteristic": {
                        characteristic.uuid: {
                            "handle": characteristic.handle,
                            "props": characteristic.properties,
                        }
                        for characteristic in service.characteristics
                    },
                }
                for service in services
            }
            _LOGGER.debug(f"{debug_services}")
            # Start loop
            while self._loop:
                await self.mug.ensure_connected()
                await self.mug.update_all()
                self.mug.updates_queued.clear()
                # Maintain connection for 5min seconds until next update
                # We will be notified of most changes during this time
                for _ in range(150):
                    await self.mug.ensure_connected()
                    await self.mug.update_queued_attributes()
                    await asyncio.sleep(2)

        except Exception as e:
            _LOGGER.error(
                f"An unexpected error occurred during loop <{type(e).__name__}>: {e}. Restarting.",
            )
            self._loop = False
            await self.mug.disconnect()
            self.hass.async_create_task(self._run())
        finally:
            await self.mug.disconnect()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mug Platform."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    coordinator = MugDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    hass.config_entries.async_setup_platforms(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry = hass.data[DOMAIN].pop(entry.entry_id)
        await entry["coordinator"].mug.disconnect()
    return unload_ok


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Anycubic component."""
    if DOMAIN not in config:
        return True

    for conf in config[DOMAIN]:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=conf,
            ),
        )
    return True
