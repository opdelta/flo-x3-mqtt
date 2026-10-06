# FLO Home MQTT Bridge

Connect your FLO home charger to Home Assistant through MQTT. Supports FLO Home
X3 status and controls, plus legacy X5 status, using FLO's unofficial cloud API.

Home Assistant discovers charger connectivity, vehicle connection, charging
status, current, voltage, session kWh, and estimated cost. Supported chargers
also expose Start/Stop buttons, Restricted Access, and a native schedule switch.
Status updates every 60 seconds; commands run immediately with a fresh read-back.

## Get started

You need a FLO account with a registered charger, an MQTT broker, and Home
Assistant's MQTT integration connected to that broker.

1. Copy the configuration template:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

2. Edit `.env` with your FLO username/password, charger name or serial
   (`FLO_STATION_NAME`), and MQTT host/port. MQTT credentials are optional if your
   broker does not require them. Keep `.env` and `data/` private.

3. Start with Docker Compose:

   ```sh
   docker compose up -d --build
   ```

The charger appears automatically under Home Assistant's MQTT integration.
Compose uses Linux host networking and persists tokens/session state in `data/`.
Run only one bridge instance per charger.

To find your charger's name or serial:

```sh
docker compose run --rm --entrypoint python flo discover_stations.py
```

View logs with `docker compose logs -f flo`. Stop with `docker compose down`.

### Without Docker

Requires Python 3.11 or newer and the same `.env` configuration:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python discover_stations.py
python main.py
```

## Weekly charging schedules

Create a Home Assistant **Schedule** helper with your allowed charging windows.
Copy [`home_assistant/charging_schedule.yaml`](home_assistant/charging_schedule.yaml)
to `config/blueprints/automation/flo/`, reload blueprints, and create an automation
selecting the helper and charger entities.

The automation keeps Restricted Access enabled and starts/stops charging around
those windows. It requires Home Assistant, MQTT, and FLO cloud access. Avoid
conflicting FLO app schedules; the **Charger Schedule** switch only toggles the
existing charger-native schedule.

## Important

- Start/Stop require an online charger and a FLO-reported connected vehicle.
  Cloud status can lag behind the physical charger.
- Restricted Access controls authorization; it is not an immediate stop command.
- Energy and cost come from FLO's session data. Missing estimates stay unknown.
- All FLO endpoints and response handling live in [`flo_client/api.py`](flo_client/api.py).
- Before making a fork public, review Git history too: older captured examples
  contain unverified device/session identifiers. Sanitizing files does not
  remove their previous versions.
