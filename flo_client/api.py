"""FLO API contracts, authentication, and translation into bridge state."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
from urllib.parse import quote

import requests
from pkce import generate_pkce_pair  # type: ignore[import-untyped]

from flo_client.consts import DATA_FOLDER

BASE_URL = "https://emobility.flo.ca"
HOMESTATION_URL = BASE_URL + "/v3.1/homestation"
SESSIONS_URL = BASE_URL + "/v3.1/user/sessions"
# Public FLO tenant and PKCE application identifiers, not user credentials.
ACCOUNT_ID = "6cedc65f-98e2-4651-bdb8-88ee4936c9ba"
CLIENT_ID = "f270e301-24ae-45fb-804d-98b9639f6183"
IDP_BASE_URL = "https://auth.pingone.ca/" + ACCOUNT_ID + "/as"
IDP_AUTHORIZE_URL = IDP_BASE_URL + "/authorize"
IDP_TOKEN_URL = IDP_BASE_URL + "/token"
SCOPE = "eMobility:all"
REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


class FloAPIError(RuntimeError):
    """The API returned an unsupported response or command."""


def _object(value: object, description: str) -> dict:
    if not isinstance(value, dict):
        raise FloAPIError(f"Expected an object for {description}.")
    return value


def _objects(value: object, description: str) -> list[dict]:
    if not isinstance(value, list):
        raise FloAPIError(f"Expected a list for {description}.")
    return [_object(item, description) for item in value]


def _string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise FloAPIError(f"Missing or invalid {description}.")
    return value


def _boolean(value: object, description: str) -> bool:
    if not isinstance(value, bool):
        raise FloAPIError(f"Missing or invalid {description}.")
    return value


def _measurement(value: object, description: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise FloAPIError(f"Invalid {description} measurement.")
    try:
        result = float(value)
    except ValueError as error:
        raise FloAPIError(f"Invalid {description} measurement.") from error
    if not math.isfinite(result):
        raise FloAPIError(f"Invalid {description} measurement.")
    return result


@dataclass(frozen=True)
class _ControlData:
    uid: str
    evse_id: str
    configurations: dict
    schedule: dict | None


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    model: str
    serial: str
    aliases: tuple[str, ...]
    online: bool
    connected: bool
    charging: bool
    state: str
    firmware: str | None = None
    can_start_stop: bool = False
    restricted_access: bool | None = None
    schedule_enabled: bool | None = None
    _control: _ControlData | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Session:
    id: str
    station_id: str
    charging: bool
    amperage: float | None
    amperage_offered: float | None
    voltage: float | None
    energy_kwh: float | None
    estimated_cost: float | None = None
    currency: str | None = None


class Auth:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.access_token: str | None = None
        self.access_token_expiry = datetime.now()
        self.token_path = Path(DATA_FOLDER) / "refresh.json"

    def is_access_token_expired(self) -> bool:
        return self.access_token_expiry <= datetime.now()

    def get_access_token(self) -> str:
        if self.access_token is not None and not self.is_access_token_expired():
            return self.access_token

        if self.token_path.exists():
            try:
                with self.token_path.open() as token_file:
                    token = _object(json.load(token_file), "saved refresh token")
                self._refresh(_string(token.get("refresh_token"), "refresh token"))
            except (OSError, ValueError, requests.RequestException, FloAPIError) as error:
                logger.warning("Token refresh failed; authenticating again: %s", error)

        if self.access_token is None or self.is_access_token_expired():
            self._authenticate()
        if self.access_token is None or self.is_access_token_expired():
            raise FloAPIError("FLO did not provide a valid access token.")
        return self.access_token

    def _json_request(self, method: str, url: str, **kwargs) -> dict:
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return _object(response.json(), "authentication response")

    def _save_token(self, token: dict) -> None:
        access_token = _string(token.get("access_token"), "access token")
        refresh_token = _string(token.get("refresh_token"), "refresh token")
        expires_in = _measurement(token.get("expires_in"), "token lifetime")
        if expires_in is None or expires_in <= 0:
            raise FloAPIError("Invalid token lifetime.")
        self.token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as token_file:
            if os.name == "posix":
                os.fchmod(token_file.fileno(), 0o600)
            json.dump({"refresh_token": refresh_token}, token_file)
        self.access_token = access_token
        self.access_token_expiry = datetime.now() + timedelta(seconds=expires_in)

    def _refresh(self, refresh_token: str) -> None:
        logger.info("Refreshing access token...")
        token = self._json_request(
            "POST",
            IDP_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
            },
        )
        self._save_token(token)

    def _authenticate(self) -> None:
        logger.info("Executing full authentication flow...")
        verifier, challenge = generate_pkce_pair()
        flow = self._json_request(
            "POST",
            IDP_AUTHORIZE_URL,
            data={
                "client_id": CLIENT_ID,
                "response_mode": "pi.flow",
                "response_type": "code",
                "scope": SCOPE,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            },
        )
        if flow.get("status") != "USERNAME_PASSWORD_REQUIRED":
            raise FloAPIError("Unexpected FLO authentication step.")
        links = _object(flow.get("_links"), "authentication links")
        check = _object(links.get("usernamePassword.check"), "password check link")
        response = requests.post(
            _string(check.get("href"), "password check URL"),
            headers={
                "Content-Type": "application/vnd.pingidentity.usernamePassword.check+json"
            },
            json={"username": self.username, "password": self.password},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        login = _object(response.json(), "password check response")
        if login.get("status") != "COMPLETED":
            raise FloAPIError("FLO authentication did not complete.")
        resumed = self._json_request(
            "GET",
            _string(login.get("resumeUrl"), "authentication resume URL"),
            cookies=response.cookies,
        )
        if resumed.get("status") != "COMPLETED":
            raise FloAPIError("FLO authorization did not complete.")
        authorization = _object(resumed.get("authorizeResponse"), "authorization")
        token = self._json_request(
            "POST",
            IDP_TOKEN_URL,
            data={
                "code": _string(authorization.get("code"), "authorization code"),
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
            },
        )
        self._save_token(token)


class FloAPI:
    def __init__(self, username: str, password: str) -> None:
        self.auth = Auth(username, password)

    def _request(self, method: str, url: str, payload: dict | None = None) -> requests.Response:
        response = requests.request(
            method,
            url,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.auth.get_access_token(),
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response

    def get_stations(self) -> list[Station]:
        data = _object(self._request("GET", HOMESTATION_URL).json(), "home stations")
        modern = _objects(data.get("ocpiHomeStations"), "OCPI home stations")
        legacy = _objects(data.get("legacyHomeStations"), "legacy home stations")
        return [self._ocpi_station(item) for item in modern] + [
            self._legacy_station(item) for item in legacy
        ]

    def _ocpi_station(self, data: dict) -> Station:
        uid = _string(data.get("chargingStationUid"), "charging station UID")
        station_id = _string(data.get("chargingStationId") or uid, "charging station ID")
        serial = _string(data.get("physicalReference"), "physical reference")
        preferences = _object(data.get("stationPreferences") or {}, "station preferences")
        configurations = _object(preferences.get("configurations") or {}, "configurations")
        name = _string(preferences.get("nickname") or serial, "station name")
        evse = _object(data.get("evse"), "EVSE")
        state = _string(evse.get("status"), "EVSE status")
        online = data.get("connectionStatus") == "Online"
        known_states = {
            "Available", "PluggedIn", "Charging", "Blocked", "Inoperative",
            "OutOfOrder", "Planned", "Reserved", "Removed", "Unknown",
        }
        if state not in known_states:
            logger.warning("Unknown FLO EVSE status: %s", state)
        restricted = configurations.get("restrictedAccess")
        restricted_access = None
        if restricted is not None:
            restricted_access = _boolean(
                _object(restricted, "restricted access").get("value"), "restricted access"
            )
        schedule = data.get("schedule")
        schedule_enabled = None
        if schedule is not None:
            schedule = _object(schedule, "schedule")
            schedule_enabled = _boolean(schedule.get("isEnabled"), "schedule enabled")
        return Station(
            id=station_id,
            name=name,
            model=_string(data.get("model"), "station model"),
            serial=serial,
            aliases=tuple(dict.fromkeys((name, serial, station_id, uid))),
            online=online,
            connected=online and state in ("PluggedIn", "Charging"),
            charging=online and state == "Charging",
            state=state,
            firmware=data.get("firmwareVersion"),
            can_start_stop="RemoteStartStop" in (evse.get("capabilities") or []),
            restricted_access=restricted_access,
            schedule_enabled=schedule_enabled,
            _control=_ControlData(
                uid=uid,
                evse_id=_string(evse.get("id"), "EVSE ID"),
                configurations=configurations,
                schedule=schedule,
            ),
        )

    def _legacy_station(self, data: dict) -> Station:
        info = _object(data.get("information"), "legacy station information")
        status = _object(data.get("status"), "legacy station status")
        configuration = _object(data.get("configuration") or {}, "legacy configuration")
        name = _string(info.get("name"), "legacy station name")
        station_id = _string(info.get("id"), "legacy station ID")
        state = _string(status.get("state"), "legacy station state")
        online = state in ("Available", "InUse")
        return Station(
            id=station_id,
            name=name,
            serial=name,
            model=_string(info.get("model"), "legacy station model"),
            aliases=tuple(dict.fromkeys((name, configuration.get("nickName") or name, station_id))),
            online=online,
            connected=online and status.get("pilotSignalState") in ("B", "C"),
            charging=online and status.get("pilotSignalState") == "C",
            state=state,
            firmware=info.get("firmware"),
        )

    def get_sessions(self) -> list[Session]:
        data = _objects(self._request("GET", SESSIONS_URL).json(), "sessions")
        sessions = []
        for item in data:
            station = _object(item.get("station"), "session station")
            energy_wh = _measurement(item.get("energyTransferredWh"), "session energy")
            cost_data = item.get("cost")
            cost = {} if cost_data is None else _object(cost_data, "session cost")
            currency = cost.get("currency")
            if currency is not None:
                currency = _string(currency, "session currency")
                if (
                    len(currency) != 3
                    or not currency.isascii()
                    or not currency.isalpha()
                    or not currency.isupper()
                ):
                    raise FloAPIError("Session currency must be a three-letter ISO currency code.")
            sessions.append(
                Session(
                    id=_string(item.get("id"), "session ID"),
                    station_id=_string(station.get("id"), "session station ID"),
                    charging=item.get("sessionState") == "Charging",
                    amperage=_measurement(item.get("amperage"), "current"),
                    amperage_offered=_measurement(item.get("amperageOffer"), "offered current"),
                    voltage=_measurement(item.get("voltage"), "voltage"),
                    energy_kwh=None if energy_wh is None else energy_wh / 1000,
                    estimated_cost=_measurement(cost.get("estimatedCost"), "estimated session cost"),
                    currency=currency,
                )
            )
        return sessions

    def get_schedule(self, station: Station) -> dict | None:
        if station._control is None:
            return None
        return deepcopy(station._control.schedule)

    def execute_command(self, station: Station, command: str, payload: str) -> None:
        control = station._control
        if control is None:
            raise FloAPIError("Remote controls are only supported for v3.1 OCPI chargers.")
        uid = quote(control.uid, safe="")
        if command in ("start", "stop"):
            if payload != "PRESS":
                raise FloAPIError("Start/stop buttons require a PRESS payload.")
            current = next(
                (item for item in self.get_stations() if item.id == station.id), None
            )
            if current is None:
                raise FloAPIError("The configured charger is no longer returned by FLO.")
            if not current.online:
                raise FloAPIError(f"Cannot {command} charging: the charger is offline.")
            if not current.connected:
                raise FloAPIError(
                    f"Cannot {command} charging: FLO does not report a connected vehicle "
                    f"(status: {current.state})."
                )
            if not current.can_start_stop or current._control is None:
                raise FloAPIError("This charger does not advertise remote start/stop.")
            control = current._control
            uid = quote(control.uid, safe="")
            body = {"evseId": control.evse_id} if command == "start" else {}
            self._request(
                "POST", f"{HOMESTATION_URL}/chargingstation/{uid}/session/{command}", body
            )
        elif command == "restrict":
            if payload not in ("ON", "OFF") or station.restricted_access is None:
                raise FloAPIError("Restricted access requires ON/OFF and a supported charger.")
            configurations = deepcopy(control.configurations)
            configurations["restrictedAccess"]["value"] = payload == "ON"
            self._request("PUT", f"{HOMESTATION_URL}/{uid}/configuration", configurations)
        elif command == "schedule":
            if payload in ("ON", "OFF"):
                if control.schedule is None:
                    raise FloAPIError("This charger does not expose a native schedule.")
                schedule = deepcopy(control.schedule)
                schedule["isEnabled"] = payload == "ON"
            else:
                schedule = _object(json.loads(payload), "schedule command")
            self._validate_schedule(schedule)
            self._request("PUT", f"{HOMESTATION_URL}/{uid}/schedule", schedule)
        else:
            raise FloAPIError(f"Unknown charger command: {command}")

    def _validate_schedule(self, schedule: dict) -> None:
        if schedule.get("kind") not in ("manual", "scheduled"):
            raise FloAPIError("Schedule kind must be manual or scheduled.")
        _boolean(schedule.get("isEnabled"), "schedule enabled")
        _objects(schedule.get("seasons"), "schedule seasons")
