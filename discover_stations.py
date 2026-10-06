#!/usr/bin/env python3
"""List charger names and serial numbers without dumping account data."""

import logging
import os

from dotenv import load_dotenv

from flo_client.client import FloX5Client


def main() -> None:
    load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    username = os.environ.get("FLO_USERNAME")
    password = os.environ.get("FLO_PASSWORD")
    if not username or not password:
        raise SystemExit("Set FLO_USERNAME and FLO_PASSWORD before discovering chargers.")
    stations = FloX5Client(username, password).get_stations()
    if not stations:
        raise SystemExit("FLO returned no home chargers. Check the account in the FLO app.")
    for station in stations:
        print(f"Name: {station.name}")
        print(f"Serial: {station.serial}")
        print(f"Model: {station.model}")
        print(f"Station ID: {station.id}")
        print(f"Online: {station.online}; state: {station.state}\n")
    print("Set FLO_STATION_NAME to a name, serial number, or station ID above.")


if __name__ == "__main__":
    main()
