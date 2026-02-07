from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    ATTR_TEMPERATURE, WaterHeaterEntityDescription
)
from homeassistant.const import (
    STATE_OFF,
    UnitOfTemperature
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .. import (
    DOMAIN,
    SIGNAL_UPDATE_DATA,
    STATUS_ON,
    COOKER_STATUS_KEEP_WARM,
    COOKER_STATUS_DELAYED_START,
)
from ..r4sconst import COOKER_PROGRAMS

STATE_BOIL = 'boil'
STATE_KEEP_WARM = 'keep_warm'
OPERATIONS_LIST = list(COOKER_PROGRAMS.keys())
OPERATIONS_LIST.append(STATE_OFF)

class RedmondCooker(WaterHeaterEntity):
    def __init__(self, kettle):
        self._kettle = kettle
        self.entity_description = WaterHeaterEntityDescription(
            key="cooker",
            name=kettle._name + " Cooker",
            icon="mdi:chef-hat",
            unit_of_measurement=UnitOfTemperature.CELSIUS
        )

        self._attr_translation_key = 'r4s'

        self._attr_unique_id = f'{DOMAIN}[{kettle._mac}][wheater][{self.entity_description.key}]'
        self._attr_device_info = DeviceInfo(connections={("mac", kettle._mac)})
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS

        self._attr_current_temperature = 0
        self._attr_target_temperature = 30
        self._attr_current_operation = STATE_OFF
        self._attr_min_temp = 30
        self._attr_max_temp = 180
        self._attr_operation_list = OPERATIONS_LIST
        self._attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE

    async def async_added_to_hass(self):
        self.update()
        self.async_on_remove(async_dispatcher_connect(self._kettle.hass, SIGNAL_UPDATE_DATA, self.update))

    def update(self):
        self._attr_target_temperature = self._kettle._tgtemp
        self._attr_current_operation = STATE_OFF

        if self._kettle._status == STATUS_ON or self._kettle._status == COOKER_STATUS_KEEP_WARM or self._kettle._status == COOKER_STATUS_DELAYED_START:
            self._attr_current_operation = 'manual'
            for key, value in COOKER_PROGRAMS.items():
                if value[0] == self._kettle._prog:
                    self._attr_current_operation = key

        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._kettle._available

    @property
    def extra_state_attributes(self):
        data = {"target_temp_step": 5}
        return data

    @property
    def current_operation(self):
        return self._attr_current_operation

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode == STATE_OFF:
            await self._kettle.modeOff()
        else:
            program = COOKER_PROGRAMS[operation_mode]
            await self._kettle.modeOnCook(program[0], program[1], program[2], program[3], program[4], program[5], program[6], program[7])

    async def async_set_manual_program(self, prog=None, subprog=None, temp=None, hours=None, minutes=None, dhours=None, dminutes=None, heat=None):
        if prog is None or subprog is None or temp is None or hours is None or minutes is None or dhours is None or dminutes is None or heat is None:
            return
        try:
            progh = self._kettle.decToHex(prog)
            subprogh = self._kettle.decToHex(subprog)
            temph = self._kettle.decToHex(temp)
            hoursh = self._kettle.decToHex(hours)
            minutesh = self._kettle.decToHex(minutes)
            dhoursh = self._kettle.decToHex(dhours)
            dminutesh = self._kettle.decToHex(dminutes)
            heath = self._kettle.decToHex(heat)
            await self._kettle.modeOnCook(progh, subprogh, temph, hoursh, minutesh, dhoursh, dminutesh, heath)
        except:
            pass

    async def async_set_timer(self, hours=None, minutes=None):
        if hours is None or minutes is None:
            return
        try:
            hoursh = self._kettle.decToHex(hours)
            minutesh = self._kettle.decToHex(minutes)
            await self._kettle.modeTimeCook(hoursh, minutesh)
        except:
            pass

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        await self._kettle.modeTempCook(self._kettle.decToHex(int(temperature)))
