"""Print normalized charger status, excluding account and authentication data."""

from dataclasses import asdict
import json
import os

from dotenv import load_dotenv

from flo_client.client import FloX5Client


if __name__ == "__main__":
    load_dotenv(".env")
    username = os.environ.get("FLO_USERNAME")
    password = os.environ.get("FLO_PASSWORD")
    name = os.environ.get("FLO_STATION_NAME")
    if not username or not password or not name:
        raise SystemExit("Set FLO_USERNAME, FLO_PASSWORD, and FLO_STATION_NAME.")
    client = FloX5Client(username, password)
    station = client.get_station_by_name(name)
    if station is None:
        raise SystemExit("Station not found. Run discover_stations.py.")
    status = asdict(station)
    status.pop("_control")
    print(json.dumps(status, indent=2))
    session = client.get_session_by_id(station.id)
    print(json.dumps(asdict(session) if session else None, indent=2))
