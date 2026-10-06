# flo-x5-mqtt

Bridge FLO home chargers to Home Assistant using MQTT discovery. Supports legacy
X5 status and modern v3.1 OCPI chargers, including the FLO Home X3.

## Configuration

Create `.env` from `.env.example` if you do not already have one. Do not commit
credentials. The application also accepts environment variables directly.

| Variable | Description |
|----------|-------------|
| `FLO_USERNAME` | FLO account username. |
| `FLO_PASSWORD` | FLO account password. |
| `FLO_STATION_NAME` | Charger nickname, physical reference/serial, or station ID. |
| `FLO_LOG_LEVEL` | Log level; defaults to `INFO`. |
| `HASS_MQTT_HOST` | MQTT broker host/IP. |
| `HASS_MQTT_PORT` | MQTT broker port, usually `1883`. |
| `HASS_MQTT_USERNAME` | Optional MQTT username. |
| `HASS_MQTT_PASSWORD` | Optional MQTT password. |

Enable the MQTT integration in Home Assistant and connect it to the same broker.
The bridge retains its existing `Flo X5: <name>` device name and sensor IDs to
avoid duplicating existing entities. The device's model identifies the actual
charger, such as FLO Home X3.

## Private configuration and public releases

Keep account credentials, the charger name/serial, and MQTT connection details
in your local `.env` (or deployment environment variables), never in source code.
Commit only the placeholder `.env.example`. On Linux, restrict local access with
`chmod 600 .env`. Environment-file variants, token/state files, private keys, and
common diagnostic captures are excluded by `.gitignore`; the Docker build uses
a source-only allowlist and never includes `.env` or `data`.

FLO's PingOne tenant ID and OAuth client ID in `flo_client/api.py` are public
application identifiers, not personal account IDs or client secrets. They remain
with the API contract so no additional private configuration is required.

Generated refresh tokens are runtime state, not manually configured credentials:
they remain in the ignored `data/refresh.json`, not `.env`. Token files are
written with owner-only permissions on Unix, including when replacing an
existing file. Treat the entire `data` directory and its backups as private.
GitHub Actions uses repository/environment secrets for registry credentials;
those values do not belong in the workflow YAML or `.env.example`.

The examples under `docs/api` and `docs/authentication` are synthetic. Do not
replace them with real API responses, debug output, or authentication captures.
Diagnostic output can contain charger/session identifiers even without tokens.

**Before publishing, review both the files being committed and Git history.**
Ignoring or sanitizing a file does not remove its older versions. Earlier
history contains captured examples with unverified charger/session identifiers.
Do not publish that history unchanged if those identifiers are private: either
start a new repository from the sanitized files without copying `.git`, or
explicitly remove sensitive content from history before pushing. Revoke/rotate
any real credentials that were previously committed; deleting them is not
enough. Secret scanners help, but do not establish whether identifiers or
account data are safe to publish.

## Run with Docker Compose

```sh
docker compose up -d --build
docker compose logs -f flo
```

This builds the updated code locally, restarts the service unless stopped, and
persists authentication/session state in `./data`. Credentials are supplied at
runtime, never included in the image. The Compose file uses Linux host networking
so the existing broker address also works if it is `localhost`; on other
platforms, use an address reachable from the container and adjust networking.

Stop with `docker compose down`. Run only one bridge instance per charger; stop
any host `python main.py` process before starting the container.

To discover charger names before starting the service:

```sh
docker compose run --rm --entrypoint python flo discover_stations.py
```

## Run without Docker

Python 3.11 or newer is required.

```sh
pip install -r requirements.txt
python discover_stations.py
python main.py
```

The `data` directory is created automatically. `debug.py` prints normalized
charger/session status without dumping authentication or account profile data.

## Home Assistant entities

The bridge publishes online, connected, charging, current, offered current,
voltage, session energy, and estimated session cost sensors.

| Sensor | FLO value |
|--------|-----------|
| **Vehicle Connected** | Reported cable state: v3.1 `evse.status` is `PluggedIn` or `Charging` while the charger is online. `Available`, `Reserved`, faults, and unknown states do not confirm a vehicle connection. Legacy X5 uses pilot states `B`/`C`. |
| **Energy Transferred** | The session's kWh added: `energyTransferredWh / 1000`, including the final reading when FLO still returns a completed session. |
| **Estimated Cost** | FLO's `cost.estimatedCost`, displayed in its `cost.currency` (for example, CAD). The bridge does not calculate or guess an electricity tariff. |

Missing measurements are reported as unknown, not fabricated zero readings.
Live sensors become unavailable after three missed polling intervals. Session
energy and cost keep their final received readings between sessions. Currency
is saved in `data/last-currency` so the retained cost keeps its unit after a
restart. Until FLO provides a session estimate and currency, cost is unknown.
Vehicle Connected also exposes the reported `charger_state` as an attribute.
This is FLO's cloud-reported state, not an independent physical cable sensor.

Modern chargers expose these additional controls when the API supports them:

| Entity | Function |
|--------|----------|
| **Start Charging** button | Requests charging; available only with an online charger and a connected vehicle. |
| **Stop Charging** button | Requests that the current charge stop; available only with an online charger and a connected vehicle. |
| **Restricted Access** switch | Changes FLO's restricted-access authorization setting. |
| **Charger Schedule** switch | Enables/disables the existing charger-native schedule. |
| **Command Result** sensor | Reports `Accepted` or `Failed`, with command/error attributes. |

Restricted Access is **not a physical disconnect** and must not be treated as an
immediate stop command. Use Stop Charging to stop an active session.

Passive polling happens every **60 seconds**. MQTT commands wake the bridge
immediately, without waiting for that interval. **Start/Stop first re-read the
charger directly from FLO**, bypassing the cache, and reject the command unless
a connected vehicle is reported. This also protects direct MQTT commands and
automations when the displayed state is stale.

An **uncached poll follows immediately** after each command, including a
rejected command or a failed/timed-out write. Commands are serialized so
start/stop ordering and refresh-token updates cannot race. Switch/sensor states
come from the read-back, never from an optimistic assumption. FLO may take time
to reflect a physical connection or accepted command; `Accepted` does not mean
the charger has already changed state. Retained MQTT commands are rejected to
prevent replay on restart.

## Set weekly schedules in Home Assistant

1. In **Settings > Devices & services > Helpers**, create a **Schedule** helper.
   Draw the allowed charging windows for each day. Split overnight windows
   across adjacent days.
2. Copy `home_assistant/charging_schedule.yaml` into Home Assistant's
   `config/blueprints/automation/flo/` directory. Reload automations/blueprints.
3. Create an automation from **FLO weekly charging schedule**, selecting your
   helper and this charger's online, connected, charging, restriction, start,
   and stop entities.

The automation keeps restricted access enabled, starts charging inside allowed
windows when a vehicle is connected, and stops charging outside those windows.
It also handles plugging in during a window and recovery after Home Assistant
or charger restarts. Editing the helper's current state triggers the same
immediate command/read-back path; it does not wait for the next passive poll.

This schedule runs in **Home Assistant**, not on the charger. It requires Home
Assistant, MQTT, and FLO cloud access to be online. Disable the automation before
manual/unrestricted operation. Avoid conflicting schedules in the FLO app.

### Charger-native schedules

The **Charger Schedule** switch preserves all existing native schedule periods
when toggled. Enabling a manual/empty schedule does not create charging windows.
Its `schedule` attribute contains the current FLO schedule document.

For advanced native schedule editing, use Home Assistant's `mqtt.publish`
action with the **Charger Schedule** entity's discovery `command_topic` and
the full native schedule JSON as the payload, with `retain: false`. That topic
accepts `ON`, `OFF`, or a JSON object containing `kind` (`manual` or `scheduled`),
`isEnabled` (boolean), and `seasons` (list of objects). Preserve FLO's existing
season/period structure rather than inventing fields. The write is followed by
the same immediate uncached read-back. Use the FLO app or its returned document
as the reference for native period structure; the weekly helper above is the
recommended graphical editor.

## API maintenance

**`flo_client/api.py` is the only place for FLO authentication, URLs, request
payloads, and response parsing.** It uses PingOne OAuth/PKCE and
`GET /v3.1/homestation` plus `GET /v3.1/user/sessions`. It handles both
`ocpiHomeStations` and `legacyHomeStations`, translating them into typed bridge
records. The MQTT layer never interprets raw FLO responses.

`flo_client/client.py` only caches/looks up those records. HTTP errors and
unexpected response shapes propagate as errors, not empty station lists.
`flo_client/auth.py` remains a compatibility import.

The cloud API is unofficial. Endpoint references:
[community API analysis](https://github.com/saxophone-k/flo-x6-mqtt/blob/main/API_ANALYSIS.md)
and [cloud client](https://github.com/saxophone-k/flo-x6-mqtt/blob/main/legacy-cloud/flo_client/client.py).

## Development

```sh
python -m unittest discover -s tests
mypy main.py discover_stations.py debug.py
```

Tests use synthetic charger data and mocked HTTP/MQTT; they never start, stop,
restrict, or reconfigure a real charger.
