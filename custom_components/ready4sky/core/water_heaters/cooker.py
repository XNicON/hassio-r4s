from homeassistant.components.water_heater import (
    ATTR_TEMPERATURE,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import STATE_OFF, UnitOfTemperature

from ..const import (
    COOKER_STATUS_DELAYED_START,
    COOKER_STATUS_KEEP_WARM,
    COOKER_STATUS_PROGRAM,
    STATUS_OFF,
    STATUS_ON,
)
from ..entity import Ready4SkyCoordinatorEntity
from ..r4sconst import COOKER_PROGRAMS

STATE_BOIL = 'boil'
STATE_KEEP_WARM = 'keep_warm'
OPERATIONS_LIST = list(COOKER_PROGRAMS.keys())
OPERATIONS_LIST.append(STATE_OFF)


class Ready4SkyCooker(Ready4SkyCoordinatorEntity, WaterHeaterEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_description = WaterHeaterEntityDescription(
            key="cooker",
            name=f"{self._device._name} Cooker",
            icon="mdi:chef-hat",
            unit_of_measurement=UnitOfTemperature.CELSIUS,
        )

        self._attr_translation_key = 'r4s'
        self._attr_unique_id = self._build_unique_id("water_heater", self.entity_description.key)
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = 30
        self._attr_max_temp = 180
        self._attr_operation_list = OPERATIONS_LIST
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )

    @property
    def target_temperature(self):
        return self.coordinator.data.get("target_temperature", 30)

    @property
    def current_temperature(self):
        return self.coordinator.data.get("current_temperature")

    @property
    def current_operation(self):
        data = self.coordinator.data
        status = data.get("status")

        if status in [STATUS_ON, COOKER_STATUS_KEEP_WARM, COOKER_STATUS_DELAYED_START]:
            prog = data.get("program")
            for key, value in COOKER_PROGRAMS.items():
                if value[0] == prog:
                    return key
            return 'manual'

        return STATE_OFF

    @property
    def extra_state_attributes(self):
        return {"target_temp_step": 5}

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode == STATE_OFF:
            self._optimistic_update(status=STATUS_OFF)
            await self._device.modeOff()
        else:
            program = COOKER_PROGRAMS[operation_mode]
            self._optimistic_update(status=COOKER_STATUS_PROGRAM, program=program[0], subprogram=program[1])
            await self._device.modeOnCook(
                program[0],
                program[1],
                program[2],
                program[3],
                program[4],
                program[5],
                program[6],
                program[7],
            )

    async def async_set_manual_program(self, prog=None, subprog=None, temp=None, hours=None, minutes=None, dhours=None, dminutes=None, heat=None):
        if prog is None or subprog is None or temp is None or hours is None or minutes is None or dhours is None or dminutes is None or heat is None:
            return

        self._optimistic_update(
            status=COOKER_STATUS_PROGRAM,
            program=self._device.decToHex(prog),
            subprogram=self._device.decToHex(subprog),
            target_temperature=int(temp),
        )
        progh = self._device.decToHex(prog)
        subprogh = self._device.decToHex(subprog)
        temph = self._device.decToHex(temp)
        hoursh = self._device.decToHex(hours)
        minutesh = self._device.decToHex(minutes)
        dhoursh = self._device.decToHex(dhours)
        dminutesh = self._device.decToHex(dminutes)
        heath = self._device.decToHex(heat)
        await self._device.modeOnCook(progh, subprogh, temph, hoursh, minutesh, dhoursh, dminutesh, heath)

    async def async_set_timer(self, hours=None, minutes=None):
        if hours is None or minutes is None:
            return

        self._optimistic_update(program_hours=int(hours), program_minutes=int(minutes))
        hoursh = self._device.decToHex(hours)
        minutesh = self._device.decToHex(minutes)
        await self._device.modeTimeCook(hoursh, minutesh)

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        self._optimistic_update(target_temperature=int(temperature))
        await self._device.modeTempCook(self._device.decToHex(int(temperature)))

    async def async_turn_on(self):
        await self.async_set_operation_mode(next(iter(COOKER_PROGRAMS.keys())))

    async def async_turn_off(self):
        await self.async_set_operation_mode(STATE_OFF)
