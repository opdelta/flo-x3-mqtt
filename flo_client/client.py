"""Cached bridge state, independent of FLO's HTTP and JSON contracts."""

from datetime import datetime, timedelta

from flo_client.api import FloAPI, FloAPIError, Session, Station
from flo_client.consts import REFRESH_DELAY_SECS


class FloX5Client:
    def __init__(self, username: str, password: str) -> None:
        self.api = FloAPI(username, password)
        self.next_refresh = datetime.min
        self._stations: list[Station] = []
        self._sessions: list[Session] = []
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        if not force and datetime.now() < self.next_refresh:
            return
        stations = self.api.get_stations()
        sessions = self.api.get_sessions()
        self._stations = stations
        self._sessions = sessions
        self.next_refresh = datetime.now() + timedelta(seconds=REFRESH_DELAY_SECS)

    def get_stations(self) -> list[Station]:
        self.refresh()
        return list(self._stations)

    def get_station_by_name(self, name: str) -> Station | None:
        self.refresh()
        matches = [station for station in self._stations if name in station.aliases]
        if len(matches) > 1:
            raise FloAPIError("Ambiguous charger name; use its serial number or station ID.")
        return matches[0] if matches else None

    def get_session_by_id(self, station_id: str) -> Session | None:
        self.refresh()
        aliases = {station_id}
        for station in self._stations:
            if station_id in station.aliases:
                aliases.update(station.aliases)
        matches = [session for session in self._sessions if session.station_id in aliases]
        return next((session for session in matches if session.charging), matches[0] if matches else None)

    def execute_command(self, station_id: str, command: str, payload: str) -> None:
        # The API adapter independently rechecks live connection state for start/stop.
        station = next((item for item in self._stations if item.id == station_id), None)
        if station is None:
            raise FloAPIError("The configured charger is no longer in the account.")
        self.api.execute_command(station, command, payload)

    def get_schedule(self, station: Station) -> dict | None:
        return self.api.get_schedule(station)

    def is_station_online(self, station: Station | None) -> bool:
        return station is not None and station.online

    def is_vehicle_connected(self, station: Station | None) -> bool:
        return station is not None and station.connected

    def is_vehicle_charging(self, station: Station | None) -> bool:
        return station is not None and station.charging
