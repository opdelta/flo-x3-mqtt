"""FLO charger MQTT bridge."""

import os
import logging
import signal
from pathlib import Path

import requests
from dotenv import load_dotenv

from flo_client.device import FloX5Device
from flo_client.consts import DATA_FOLDER

logger = logging.getLogger(__name__)


def configure_logging(log_level: str | None) -> None:
    level = logging.INFO
    if log_level:
        level = logging.getLevelName(log_level)

    format = "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s"

    logging.basicConfig(level=level, format=format)


def main() -> int:
    # Load environment variables from .env file if it exists
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    # Get settings from the environment.
    username = os.environ.get("FLO_USERNAME")
    password = os.environ.get("FLO_PASSWORD")
    station_name = os.environ.get("FLO_STATION_NAME")
    log_level = os.environ.get("FLO_LOG_LEVEL")
    hass_mqtt_host = os.environ.get("HASS_MQTT_HOST")
    hass_mqtt_port = os.environ.get("HASS_MQTT_PORT")
    hass_mqtt_username = os.environ.get("HASS_MQTT_USERNAME")
    hass_mqtt_password = os.environ.get("HASS_MQTT_PASSWORD")

    configure_logging(log_level)

    Path(DATA_FOLDER).mkdir(mode=0o700, parents=True, exist_ok=True)

    # Validate the environment variables, the MQTT username and password are optional.
    if not username:
        raise ValueError("FLO_USERNAME environment variable not set.")
    if not password:
        raise ValueError("FLO_PASSWORD environment variable not set.")
    if not station_name:
        raise ValueError("FLO_STATION_NAME environment variable not set.")
    if not hass_mqtt_host:
        raise ValueError("HASS_MQTT_HOST environment variable not set.")
    if not hass_mqtt_port:
        raise ValueError("HASS_MQTT_PORT environment variable not set.")

    logger.info("Starting FLO to MQTT...")
    device = None
    try:
        device = FloX5Device(
            username,
            password,
            station_name,
            hass_mqtt_host,
            hass_mqtt_port,
            hass_mqtt_username,
            hass_mqtt_password,
        )

        signal.signal(signal.SIGTERM, lambda *_: device.stop())
        device.run()
    except KeyboardInterrupt:
        logger.info("Stopping FLO to MQTT...")
    except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
        logger.error("Error: %s", error)
        return 1
    finally:
        if device is not None:
            device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
