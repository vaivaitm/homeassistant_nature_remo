import logging
import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .coordinator import NatureRemoCoordinator  # 追加！
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_TOKEN = "token"
CONF_NAME = "name"
CONF_DEVICE_ID = "device_id"
CONF_APPLIANCE_ID = "appliance_id"

MODE_MAP = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "warm",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "blow",
    HVACMode.AUTO: "auto",
}

PLATFORM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_APPLIANCE_ID): cv.string,
    }
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """
    UI設定からエアコンエンティティを追加.
    Add air conditioner entity from UI configuration.
    """
    _LOGGER.info("Nature Remo Climate: async_setup_entry called!")
    _LOGGER.debug(f"★[Climate]{hass.data[DOMAIN][entry.entry_id]}")
    _LOGGER.debug(f"config_entry.options: {entry.options}")

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NatureRemoCoordinator = data["coordinator"]
    api = data["api"]

    entities = []

    for appliance in coordinator.aircons.values():

        entity = NatureRemoClimate(
            coordinator=coordinator,
            appliance=appliance,
            device=appliance["device"],
            api=api,
        )
        entities.append(entity)

    if not entities:
        _LOGGER.warning("No climate appliances matched selected IDs.")

    async_add_entities(entities, True)


class NatureRemoClimate(ClimateEntity):
    """
    Nature Remoでエアコンを操作するエンティティ.
    Entity to control an air conditioner via Nature Remo.
    """

    def __init__(
        self, coordinator: NatureRemoCoordinator, appliance, device, api
    ) -> None:
        """エアコンの初期設定. / Initialize air conditioner settings."""
        _LOGGER.debug(f'[{appliance["name"]}]Start __init__')
        try:
            self._attr_unique_id = f"nature_remo_climate_{appliance["appliance_id"]}"
            self._attr_name = f"Nature Remo {appliance["name"]}"
            self._coordinator = coordinator  # コーディネーターを使う
            self._appliance = appliance
            self._device = device
            self._appliance_id = appliance["appliance_id"]
            self._temperature = None
            self._humidity = None
            self._hvac_modes = [HVACMode.OFF]
            self._hvac_mode = HVACMode.OFF
            self._button = "power-off"
            self._api = api
            self._target_temperature = 25  # 初期温度を 25℃ に設定
            self._fan_mode = "auto"
            self._swing_mode = "auto"
            self._aircon_range_modes = {}

        except Exception as e:
            _LOGGER.error(f"Error initializing NatureRemoClimate: {e}")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device["device_id"])},
            "name": self._device["name"],
            "manufacturer": "Nature",
            "model": self._device.get("firmware_version", "Nature Remo"),
        }

    @property
    def supported_features(self) -> int:
        """対応している機能を定義. / Define the features supported by this entity."""
        _LOGGER.debug(f"[{self._attr_name}] Start supported_features")
        support_feature = ClimateEntityFeature(0)
        if self.min_temp != 0.0 and self.max_temp != 0.0:
            support_feature = support_feature | ClimateEntityFeature.TARGET_TEMPERATURE
        if self.fan_modes:
            support_feature = support_feature | ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            support_feature = support_feature | ClimateEntityFeature.SWING_MODE

        _LOGGER.info(
            f"Nature Remo Climate support_feature: {support_feature}"
        )  # ログに出す
        return support_feature

    @property
    def target_temperature_step(self) -> float:
        """温度変更の刻み幅を設定. / Set the step size for temperature adjustment."""
        _LOGGER.debug(f"[{self._attr_name}] Start target_temperature_step")
        # 1. 温度リスト（文字列）を小数で処理できるように変換
        remo_mode = MODE_MAP.get(self._hvac_mode)
        temp_list = self._aircon_range_modes.get(remo_mode, {}).get("temp", [])
        temp_list = list(map(float, filter(None, temp_list)))

        if not temp_list:
            return 0.0

        # 2. 隣り合う要素の差を計算
        differences = [
            temp_list[i + 1] - temp_list[i] for i in range(len(temp_list) - 1)
        ]

        # 3. 刻み幅を特定（すべて同じなら、それが刻み幅）
        step = 1.0
        if len(set(differences)) == 1:
            step = differences[0]  # すべて同じならその値が刻み幅
        _LOGGER.debug(f"target_temperature_step: {step}")
        return step

    @property
    def min_temp(self):
        """設定可能な最低温度. / Return the minimum temperature that can be set."""
        _LOGGER.debug(f"[{self._attr_name}] Start min_temp")
        # 温度リスト（文字列）を小数で処理できるように変換
        remo_mode = MODE_MAP.get(self._hvac_mode)
        temp_list = self._aircon_range_modes.get(remo_mode, {}).get("temp", [])
        temp_list = list(map(float, filter(None, temp_list)))
        if not temp_list:
            return 0.0

        # 最小値を取得
        _LOGGER.debug(f"min_temp: {min(temp_list)}")
        return min(temp_list)  # 最小値

    @property
    def max_temp(self):
        """設定可能な最高温度. / Return the maximum temperature that can be set."""
        # 温度リスト（文字列）を小数で処理できるように変換
        remo_mode = MODE_MAP.get(self._hvac_mode)
        temp_list = self._aircon_range_modes.get(remo_mode, {}).get("temp", [])
        temp_list = list(map(float, filter(None, temp_list)))
        if not temp_list:
            return 0.0

        # 最大値を取得
        _LOGGER.debug(f"max_temp: {max(temp_list)}")
        return max(temp_list)  # 最大値

    @property
    def current_temperature(self) -> float | None:
        """現在の室温を返す / Return the current room temperature."""
        return self._temperature

    @property
    def current_humidity(self) -> int | None:
        """現在の湿度を返す / Return the current room humidity."""
        return self._humidity

    @property
    def name(self):
        """エアコンの表示名を返す. / Return the display name of the air conditioner."""
        return self._attr_name

    @property
    def temperature_unit(self) -> str:
        """温度の単位を取得. / Get the temperature unit used by the device."""
        return UnitOfTemperature.CELSIUS

    @property
    def hvac_mode(self):
        """現在の動作モード. / Current operation mode of the air conditioner."""
        if self._button == "power-off":
            return HVACMode.OFF
        return self._hvac_mode

    @property
    def hvac_modes(self):
        """サポートするモード. / List of supported HVAC modes."""
        return self._hvac_modes

    @property
    def fan_modes(self) -> list[str] | None:
        """設定可能な風量のリスト. / List of available fan modes."""
        remo_mode = MODE_MAP.get(self._hvac_mode)
        return self._aircon_range_modes.get(remo_mode, {}).get("vol", [])

    @property
    def swing_modes(self) -> list[str] | None:
        """設定可能な風向きのリスト. / List of available swing modes."""
        remo_mode = MODE_MAP.get(self._hvac_mode)
        return self._aircon_range_modes.get(remo_mode, {}).get("dir", [])

    @property
    def target_temperature(self) -> float | None:
        """現在の目標温度を取得. / Get the current target temperature."""
        return self._target_temperature

    @property
    def fan_mode(self) -> str | None:
        """現在の風量を返す. / Return the current fan mode."""
        return self._fan_mode

    @property
    def swing_mode(self) -> str | None:
        """現在の風向きを返す. / Return the current swing mode."""
        return self._swing_mode

    def update_status(self) -> None:
        """
        コーディネーターで取得した値に更新する.
        設定で指定された外部エンティティの値があればそれを優先して温度・湿度に反映する.
        Update values using the data from the coordinator.
        If there are values from an external entity specified in the settings,
        those will take priority and be reflected in the temperature and humidity.
        """
        _LOGGER.debug(f"[{self._attr_name}] Start update_status.")
        appliance = self._coordinator.data.get(self._appliance_id, {})


        # デバイスごとの温度・湿度エンティティIDを options から取得
        external_temperature_entity = None
        external_humidity_entity = None
        temperature_set = False
        humidity_set = False
        try:
            hass = getattr(self._coordinator, "hass", None)
            config_entry = None
            if hass:
                for entry in hass.config_entries.async_entries(DOMAIN):
                    if entry.options:
                        id = self.device_entry.id
                        temp_key = f"{id}_temperature_entity"
                        hum_key = f"{id}_humidity_entity"
                        external_temperature_entity = entry.options.get(temp_key)
                        external_humidity_entity = entry.options.get(hum_key)
                        break
            # Home Assistantのstateから外部エンティティの値を取得
            if external_temperature_entity and hass:
                state = hass.states.get(external_temperature_entity)
                if state and state.state not in (None, "unknown", "unavailable"):
                    self._temperature = float(state.state)
                    temperature_set = True
            if external_humidity_entity and hass:
                state = hass.states.get(external_humidity_entity)
                if state and state.state not in (None, "unknown", "unavailable"):
                    self._humidity = int(float(state.state))
                    humidity_set = True
        except Exception as e:
            _LOGGER.error(f"Error getting external entity value: {e}")

        # 外部エンティティで値がセットされていなければ、Climateエンティティに紐づくデバイスから温度、湿度を取得する
        device = self._coordinator.devices[self._device["device_id"]].get("events", {})
        if device:
            # 室温
            if not temperature_set and "te" in device:
                self._temperature = device["te"].get("val")

            # 湿度
            if not humidity_set and "hu" in device:
                self._humidity = device["hu"].get("val")

        # settingsから取得できる情報
        if appliance and "settings" in appliance:
            _LOGGER.info("***Nature Remo Settings: %s", appliance["settings"])
            # 動作モード
            self._hvac_mode = self.get_remo_mode_to_hvac_mode(
                appliance["settings"].get("mode", "")
            )
            # ボタン（OFFボタン）
            self._button = appliance["settings"].get("button", "")

            # 目標温度
            temp = appliance["settings"].get("temp", "20.0")
            try:
                self._target_temperature = float(temp)
            except (ValueError, TypeError):
                self._target_temperature = 0.0

            # 風量
            self._fan_mode = appliance["settings"].get("vol", "auto")
            # 風向き
            self._swing_mode = appliance["settings"].get("dir", "auto")

        # aircon_range_mode
        if appliance and "aircon" in appliance:
            self._aircon_range_modes = (
                appliance["aircon"].get("range", {}).get("modes", {})
            )
            if self._aircon_range_modes:
                # 動作モード
                set_range_modes = [HVACMode.OFF]
                if self._aircon_range_modes.get("cool", {}):
                    set_range_modes.append(HVACMode.COOL)
                if self._aircon_range_modes.get("dry", {}):
                    set_range_modes.append(HVACMode.DRY)
                if self._aircon_range_modes.get("warm", {}):
                    set_range_modes.append(HVACMode.HEAT)
                if self._aircon_range_modes.get("blow", {}):
                    set_range_modes.append(HVACMode.FAN_ONLY)
                if self._aircon_range_modes.get("auto", {}):
                    set_range_modes.append(HVACMode.AUTO)
                self._hvac_modes = set_range_modes

        self.async_write_ha_state()

    def get_remo_mode_to_hvac_mode(self, remo_mode) -> HVACMode | None:
        """
        Nature Remoの動作モードをHomeAssistantの動作モードに変換する.
        Convert Nature Remo operation mode to Home Assistant HVAC mode.
        """
        return next(
            (key for key, value in MODE_MAP.items() if value == remo_mode),
            None,
        )

    async def async_added_to_hass(self):
        """
        エンティティがHome Assistantに追加されたら更新をトリガー.
        Trigger update when the entity is added to Home Assistant.
        """
        _LOGGER.info(
            f"[{self._attr_name}] async_added_to_hass: Climate entity complete setup"
        )
        self.async_on_remove(self._coordinator.async_add_listener(self.update_status))
        self.update_status()
        self.async_write_ha_state()  # 状態をHome Assistantに通知

    async def async_set_hvac_mode(self, hvac_mode):
        """エアコンのモードを変更. / Change the operation mode of the air conditioner."""
        _LOGGER.info("Setting HVAC mode: %s", hvac_mode)
        if hvac_mode not in self.hvac_modes:
            _LOGGER.warning("Unsupported HVAC mode: %s", hvac_mode)
            return

        if hvac_mode == HVACMode.OFF:
            payload = {"button": "power-off"}
            self._button = "power-off"
        else:
            operation_mode = MODE_MAP.get(hvac_mode)
            payload = {"operation_mode": operation_mode}
            self._button = ""
            self._hvac_mode = hvac_mode  # 状態を更新

        response = await self._api.send_command_climate(
            payload, self._appliance_id
        )  # APIを非同期で送信
        _LOGGER.info("Set HVACMode: %s", response)
        # レスポンス情報をもとに現在の状態を更新する
        # 設定温度
        self._hvac_mode = self.get_remo_mode_to_hvac_mode(response.get("mode", ""))
        if self._hvac_mode is HVACMode.FAN_ONLY:
            temp = "0.0"
        else:
            temp = response.get("temp", "25.0")
        self._target_temperature = float(temp)
        self._fan_mode = response.get("vol", "auto")
        self._swing_mode = response.get("dir", "auto")
        self._button = response.get("button", "")

        self.async_write_ha_state()  # 状態をHome Assistantに通知

    async def async_set_temperature(self, **kwargs):
        """エアコンの温度を変更. / Change the temperature setting of the air conditioner."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            _LOGGER.warning("温度が指定されていません！")
            return

        # self._hvac_mode` を API 用の `operation_mode` に変換
        operation_mode = MODE_MAP.get(self._hvac_mode)
        if operation_mode is None:
            _LOGGER.error("Invalid HVAC mode: %s", self._hvac_mode)
            return

        _LOGGER.debug("Setting temperature to: %s", temperature)

        set_temperature = self.format_temperature(temperature)
        payload = {
            "operation_mode": operation_mode,  # 現在のモードを維持
            "temperature": set_temperature,
        }

        await self._api.send_command_climate(payload, self._appliance_id)
        self._target_temperature = temperature  # 状態を更新
        self._button = ""  # 温度設定を変更したらエアコンをONにする
        self.async_write_ha_state()

    def format_temperature(self, value: float) -> str:
        if value.is_integer():
            return str(int(value))
        else:
            return str(value)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """風量を変更. / Change the fan mode."""
        # `self._hvac_mode` を API 用の `operation_mode` に変換
        operation_mode = MODE_MAP.get(self._hvac_mode)
        if operation_mode is None:
            _LOGGER.error("Invalid HVAC mode: %s", self._hvac_mode)
            return

        payload = {
            "operation_mode": operation_mode,  # 現在のモードを維持
            "air_volume": fan_mode,
        }

        await self._api.send_command_climate(
            payload, self._appliance_id
        )  # API で風量を変更！

        self._fan_mode = fan_mode
        self._button = ""  # 温度設定を変更したらエアコンをONにする
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """風向きを変更. / Change the swing mode."""
        # `self._hvac_mode` を API 用の `operation_mode` に変換
        operation_mode = MODE_MAP.get(self._hvac_mode)
        if operation_mode is None:
            _LOGGER.error("Invalid HVAC mode: %s", self._hvac_mode)
            return

        payload = {
            "operation_mode": operation_mode,  # 現在のモードを維持
            "air_direction": swing_mode,
        }

        await self._api.send_command_climate(
            payload, self._appliance_id
        )  # API で風向きを変更！

        self._swing_mode = swing_mode  # Home Assistant に反映！
        self._button = ""  # 温度設定を変更したらエアコンをONにする
        self.async_write_ha_state()
