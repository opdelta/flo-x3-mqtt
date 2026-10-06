"""Home Assistant entities and immediate, serialized MQTT command handling."""

import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Event

import requests
from paho.mqtt.client import Client, MQTTMessage  # type: ignore[import-untyped]

from flo_client.api import FloAPIError, Session, Station
from flo_client.client import FloX5Client
from flo_client.consts import DATA_FOLDER, REFRESH_DELAY_SECS
from ha_mqtt_discoverable import Settings, DeviceInfo, Discoverable  # type: ignore
from ha_mqtt_discoverable.sensors import (  # type: ignore
    BinarySensor,
    BinarySensorInfo,
    Button,
    ButtonInfo,
    Sensor,
    SensorInfo,
    Switch,
    SwitchInfo,
)


class FloX5Device:
    def __init__(
        self,
        username: str,
        password: str,
        station_name: str,
        hass_mqtt_host: str,
        hass_mqtt_port: str,
        hass_mqtt_username: str | None,
        hass_mqtt_password: str | None,
    ) -> None:
        self.username: str = username
        self.password: str = password
        self.station_name: str = station_name
        self.hass_mqtt_host: str = hass_mqtt_host
        self.hass_mqtt_port: str = hass_mqtt_port
        self.hass_mqtt_username: str | None = hass_mqtt_username
        self.hass_mqtt_password: str | None = hass_mqtt_password

        self.logger: logging.Logger = logging.getLogger(__name__)
        self._commands: Queue[tuple[str, str]] = Queue()
        self._stopped = Event()
        self.entities: list[Discoverable] = []
        self.controls: list[Button | Switch] = []
        self.session_buttons: list[Button] = []
        self.restricted_access_switch: Switch | None = None
        self.schedule_switch: Switch | None = None
        self.client: FloX5Client = FloX5Client(username, password)
        self._initialize_device()
        self._initialize_sensors()
        self._initialize_controls()
        for entity in self.entities:
            publication = entity.write_config()
            if publication is not None:
                publication.wait_for_publish(timeout=10)
                if not publication.is_published():
                    raise RuntimeError("Timed out publishing MQTT discovery.")
        if self.estimated_cost_settings.entity.unit_of_measurement is None:
            self.estimated_cost_sensor.set_state("None")

    def _initialize_device(self) -> None:
        # Configure the required parameters for the MQTT broker
        self.mqtt_settings = Settings.MQTT(
            host=self.hass_mqtt_host,
            port=self.hass_mqtt_port,
            username=self.hass_mqtt_username,
            password=self.hass_mqtt_password,
        )

        station = self.client.get_station_by_name(self.station_name)

        if station is None:
            names = ", ".join(item.name for item in self.client.get_stations())
            raise FloAPIError(
                f"Station not found: {self.station_name}. Available chargers: {names or '(none)'}"
            )
        self.station_id = station.id
        self.logger.info("Creating device for station: %s (%s)", station.name, station.model)

        # Define the device. At least one of `identifiers` or `connections` must be supplied
        self.device_info = DeviceInfo(
            name="Flo X5: " + station.name,
            model=station.model,
            manufacturer="AddEnergie",
            identifiers=station.id,
            sw_version=station.firmware,
        )

    def _initialize_sensors(self) -> None:
        self.logger.info("Initializing sensors...")

        # Online sensor
        online_sensor_info = BinarySensorInfo(
            name="Station Online",
            device_class="connectivity",
            unique_id="status",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )
        online_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=online_sensor_info
        )

        self.online_sensor = BinarySensor(online_sensor_settings)

        # Vehicle connected sensor
        vehicle_connected_sensor_info = BinarySensorInfo(
            name="Vehicle Connected",
            device_class="plug",
            unique_id="vehicle_connected",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )
        vehicle_connected_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=vehicle_connected_sensor_info
        )

        self.vehicle_connected_sensor = BinarySensor(vehicle_connected_sensor_settings)

        # Charging sensor
        vehicle_charging_sensor_info = BinarySensorInfo(
            name="Vehicle Charging",
            device_class="battery_charging",
            unique_id="vehicle_charging",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )
        vehicle_charging_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=vehicle_charging_sensor_info
        )

        self.vehicle_charging_sensor = BinarySensor(vehicle_charging_sensor_settings)

        # Amperage sensor
        amperage_charging_sensor_info = SensorInfo(
            name="Amperage",
            unit_of_measurement="A",
            state_class="measurement",
            device_class="current",
            unique_id="amperage_charging",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )

        amperage_charging_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=amperage_charging_sensor_info
        )

        self.amperage_charging_sensor = Sensor(amperage_charging_sensor_settings)

        # Amperage offered sensor
        amperage_offered_sensor_info = SensorInfo(
            name="Amperage Offered",
            unit_of_measurement="A",
            state_class="measurement",
            device_class="current",
            unique_id="amperage_offered",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )

        amperage_offered_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=amperage_offered_sensor_info
        )

        self.amperage_offered_sensor = Sensor(amperage_offered_sensor_settings)

        # Voltage sensor
        voltage_sensor_info = SensorInfo(
            name="Voltage",
            unit_of_measurement="V",
            state_class="measurement",
            device_class="voltage",
            unique_id="voltage",
            device=self.device_info,
            expire_after=REFRESH_DELAY_SECS * 3,
        )

        voltage_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=voltage_sensor_info
        )

        self.voltage_sensor = Sensor(voltage_sensor_settings)

        # Energy transferred sensor
        energy_transferred_sensor_info = SensorInfo(
            name="Energy Transferred",
            unit_of_measurement="kWh",
            state_class="total_increasing",
            device_class="energy",
            unique_id="session_energy_transferred",
            device=self.device_info,
        )

        energy_transferred_sensor_settings = Settings(
            mqtt=self.mqtt_settings, entity=energy_transferred_sensor_info
        )

        self.energy_transferred_sensor = Sensor(energy_transferred_sensor_settings)
        self._saved_cost_currency = self._get_last_currency()
        session = self.client.get_session_by_id(self.station_id)
        currency = session.currency if session is not None else None
        currency = currency or self._saved_cost_currency
        self.estimated_cost_settings: Settings[SensorInfo] = Settings(
            mqtt=self.mqtt_settings,
            entity=SensorInfo(
                name="Estimated Cost",
                unique_id=f"{self.station_id}_estimated_cost",
                device=self.device_info,
                device_class="monetary" if currency else None,
                unit_of_measurement=currency,
            ),
        )
        self.estimated_cost_sensor = Sensor(self.estimated_cost_settings)
        self.entities.extend(
            (
                self.online_sensor,
                self.vehicle_connected_sensor,
                self.vehicle_charging_sensor,
                self.amperage_charging_sensor,
                self.amperage_offered_sensor,
                self.voltage_sensor,
                self.energy_transferred_sensor,
                self.estimated_cost_sensor,
            )
        )

    def _initialize_controls(self) -> None:
        station = self._get_station()
        self.command_result_sensor = Sensor(
            Settings(
                mqtt=self.mqtt_settings,
                entity=SensorInfo(
                    name="Command Result",
                    unique_id=f"{self.station_id}_command_result",
                    entity_category="diagnostic",
                    device=self.device_info,
                ),
            )
        )
        self.entities.append(self.command_result_sensor)
        if station.can_start_stop:
            for name, command in (("Start Charging", "start"), ("Stop Charging", "stop")):
                button = Button(
                    Settings(
                        mqtt=self.mqtt_settings,
                        manual_availability=True,
                        entity=ButtonInfo(
                            name=name,
                            unique_id=f"{self.station_id}_{command}",
                            device=self.device_info,
                            retain=False,
                            qos=1,
                        ),
                    ),
                    self._on_command,
                    command,
                )
                self.controls.append(button)
                self.session_buttons.append(button)
        if station.restricted_access is not None:
            self.restricted_access_switch = self._make_switch("Restricted Access", "restrict")
        if station.schedule_enabled is not None:
            self.schedule_switch = self._make_switch("Charger Schedule", "schedule")
        self.entities.extend(self.controls)

    def _make_switch(self, name: str, command: str) -> Switch:
        switch = Switch(
            Settings(
                mqtt=self.mqtt_settings,
                manual_availability=True,
                entity=SwitchInfo(
                    name=name,
                    unique_id=f"{self.station_id}_{command}",
                    device=self.device_info,
                    optimistic=False,
                    retain=False,
                    qos=1,
                ),
            ),
            self._on_command,
            command,
        )
        self.controls.append(switch)
        return switch

    def _on_command(self, client: Client, command: str, message: MQTTMessage) -> None:
        if message.retain:
            self.logger.warning("Ignoring retained %s command to prevent replay.", command)
            return
        try:
            payload = message.payload.decode("utf-8")
        except UnicodeDecodeError:
            self.logger.error("Ignoring non-UTF-8 %s command.", command)
            return
        self._commands.put((command, payload))

    def _get_station(self) -> Station:
        station = self.client.get_station_by_name(self.station_id)
        if station is None:
            raise FloAPIError("The configured charger is no longer returned by FLO.")
        return station

    def _save_last_session_id(self, session_id: str) -> None:
        (Path(DATA_FOLDER) / "last-session").write_text(session_id)

    def _get_last_session_id(self) -> str | None:
        path = Path(DATA_FOLDER) / "last-session"
        return path.read_text() if path.exists() else None

    def _get_last_currency(self) -> str | None:
        path = Path(DATA_FOLDER) / "last-currency"
        if not path.exists():
            return None
        currency = path.read_text().strip()
        if (
            len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not currency.isupper()
        ):
            self.logger.warning("Ignoring invalid saved currency in %s; awaiting FLO data.", path)
            return None
        return currency

    def update_all_sensors(self, force: bool = False) -> None:
        self.logger.info("Updating sensors...")
        self.client.refresh(force=force)
        station = self._get_station()
        session = self.client.get_session_by_id(station.id)

        if station.online:
            self.online_sensor.on()
        else:
            self.online_sensor.off()
        self.online_sensor.set_attributes({"charger_state": station.state})
        if station.connected:
            self.vehicle_connected_sensor.on()
        else:
            self.vehicle_connected_sensor.off()
        self.vehicle_connected_sensor.set_attributes({"charger_state": station.state})
        self._update_charging(station, session)

        for control in self.controls:
            available = station.online
            if control in self.session_buttons:
                available = available and station.connected and station.can_start_stop
            control.set_availability(available)
        if self.restricted_access_switch is not None:
            if station.restricted_access is None:
                self.restricted_access_switch.set_availability(False)
            elif station.restricted_access:
                self.restricted_access_switch.on()
            else:
                self.restricted_access_switch.off()
        if self.schedule_switch is not None:
            if station.schedule_enabled is None:
                self.schedule_switch.set_availability(False)
            elif station.schedule_enabled:
                self.schedule_switch.on()
            else:
                self.schedule_switch.off()
            self.schedule_switch.set_attributes({"schedule": self.client.get_schedule(station)})

    def _update_charging(self, station: Station, session: Session | None) -> None:
        if station.charging:
            self.vehicle_charging_sensor.on()
        else:
            self.vehicle_charging_sensor.off()
        if station.charging and session is None:
            self.logger.warning("FLO reports charging but has not returned session measurements.")
        readings = (
            (self.amperage_charging_sensor, session.amperage if session else None),
            (self.amperage_offered_sensor, session.amperage_offered if session else None),
            (self.voltage_sensor, session.voltage if session else None),
        )
        for sensor, value in readings:
            sensor.set_state(0 if not station.charging else "None" if value is None else value)
        if session is not None and session.energy_kwh is not None:
            if session.id != self._get_last_session_id():
                self.energy_transferred_sensor.set_state(0)
                self._save_last_session_id(session.id)
            self.energy_transferred_sensor.set_state(f"{session.energy_kwh:.2f}")
        elif session is not None or station.charging:
            self.energy_transferred_sensor.set_state("None")
        self._update_estimated_cost(station, session)

    def _update_estimated_cost(self, station: Station, session: Session | None) -> None:
        if session is None:
            if station.charging:
                self.estimated_cost_sensor.set_state("None")
            return

        if session.currency is not None:
            if session.currency != self._saved_cost_currency:
                (Path(DATA_FOLDER) / "last-currency").write_text(session.currency)
                self._saved_cost_currency = session.currency
            if self.estimated_cost_settings.entity.unit_of_measurement != session.currency:
                self.estimated_cost_sensor.set_state("None")
                self.estimated_cost_settings.entity.unit_of_measurement = session.currency
                self.estimated_cost_settings.entity.device_class = "monetary"
                self.estimated_cost_sensor.write_config()

        if session.estimated_cost is None or session.currency is None:
            if session.estimated_cost is not None:
                self.logger.warning("FLO returned an estimated cost without a currency.")
            self.estimated_cost_sensor.set_state("None")
        else:
            self.estimated_cost_sensor.set_state(session.estimated_cost)

    def _poll_safely(self, force: bool = False) -> bool:
        try:
            self.update_all_sensors(force=force)
        except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
            self.logger.error("Error updating charger state: %s", error)
            for control in self.controls:
                control.set_availability(False)
            return False
        return True

    def process_command(self, timeout: float = REFRESH_DELAY_SECS) -> bool:
        try:
            command, payload = self._commands.get(timeout=timeout)
        except Empty:
            return False
        if self._stopped.is_set():
            return True
        errors = []
        try:
            self.client.execute_command(self.station_id, command, payload)
        except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
            self.logger.error("Charger command %s failed: %s", command, error)
            errors.append(str(error))
        # A timed-out write may still have reached FLO; refresh even after an error.
        if not self._poll_safely(force=True):
            errors.append("Unable to read back charger state.")
        self.command_result_sensor.set_attributes(
            {"command": command, "error": "; ".join(errors)}
        )
        self.command_result_sensor.set_state("Failed" if errors else "Accepted")
        return True

    def run(self) -> None:
        self._poll_safely()
        while not self._stopped.is_set():
            if not self.process_command():
                self._poll_safely()

    def stop(self) -> None:
        self._stopped.set()
        self._commands.put(("", ""))

    def close(self) -> None:
        for control in self.controls:
            control.set_availability(False)
        for entity in self.entities:
            entity.mqtt_client.disconnect()
            entity.mqtt_client.loop_stop()
