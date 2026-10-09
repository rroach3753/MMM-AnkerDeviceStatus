# MMM-AnkerDeviceStatus — ALPHA

**Status: ALPHA (`1.0.0-alpha.1`) — testing is not complete.**

This module is available for community testing, not as a production-ready
release. Automated tests and a sample browser layout have been verified, but
authentication, telemetry, and compatibility with real Anker devices have not
yet been verified. Device support below reflects the upstream client's
capabilities, not completed end-to-end testing of this module. The module will
remain ALPHA until live-device testing is completed.

Report test results and problems through
[GitHub issues](https://github.com/rroach3753/MMM-AnkerDeviceStatus/issues).
Include the model, firmware, runtime versions, and a sanitized error description.
Never upload credentials, account tokens, authentication caches, or unredacted
device/account data.

A read-only MagicMirror² module for Anker SOLIX systems and supported Wi-Fi
power devices registered in the **Anker app**. Displays device names, connection
status, battery percentage, input/output watts, available solar power, and
optional temperature. Includes device filtering, custom names, compact layout,
low-battery highlighting, and explicit unavailable/stale-data messages.

Open [examples/preview.html](examples/preview.html) in a browser for a sample
layout without credentials. All values in that preview are fictional.

## Compatibility

Uses the unofficial [anker-solix-api](https://github.com/thomluther/anker-solix-api)
client, pinned to a source revision verified by local automated tests only. Anker can change its cloud services
without notice; this is not an official Anker integration.

- SOLIX Solarbank, supported portable power stations, power panels and X1 systems.
- Supported Anker smart plugs, smart meters and Wi-Fi chargers, including the
  A2345 Prime 250W charger and A91B2 charging station.
- Actual support and available measurements depend on the client library,
  model, firmware, account permissions and whether the device reports to the
  cloud. See the upstream [supported device list](https://github.com/thomluther/anker-solix-api#supported-devices).
- Wi-Fi alone is **not** sufficient: unrelated brands, Eufy/Soundcore devices,
  and Bluetooth-only chargers/power banks are not supported.

Many standalone devices provide almost no telemetry through the HTTP API.
This module also subscribes to **passive MQTT telemetry**, using the account's
Anker-issued MQTT credentials. It does not send device controls, status-request
commands, or real-time activation commands. Some models only publish useful
readings while awake or while the app is open; readings may remain unavailable.
An account listing does not prove a device is online.

## Requirements

- MagicMirror² with Node.js 20+.
- Python 3.12 or newer (upstream currently supports 3.12–3.14), pip and Git.
- Internet access to Anker's HTTPS API and MQTT service.
- An Anker email/password account and its correct two-letter country code.
  Use the owner account for the most complete device discovery.

## Installation

On the **machine running MagicMirror**, clone the ALPHA module into your
MagicMirror modules directory:

```sh
cd ~/MagicMirror/modules
git clone https://github.com/rroach3753/MMM-AnkerDeviceStatus.git
cd MMM-AnkerDeviceStatus
```

Adjust the MagicMirror path if your installation is elsewhere. Then install:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Edit `.env` locally:

```dotenv
ANKER_EMAIL=you@example.com
ANKER_PASSWORD="your-password"
ANKER_COUNTRY=US
```

Quote passwords containing `#`, spaces or other dotenv-special characters.
Do not paste credentials into MagicMirror's browser-visible configuration.
The Python helper loads `.env` from this module directory. Existing environment
variables take precedence. Protect the module's `.venv` directory too: the
upstream library caches account tokens inside its package's `authcache` folder.
The helper creates new files with owner-only permissions.

The Node helper uses the module's `.venv` by default. To use a different
interpreter, set `ANKER_PYTHON` to its absolute path in the **environment that
launches MagicMirror** (not in `.env`, which is loaded after Python starts).
For example, configure your service/PM2 environment and restart MagicMirror.

Add to your MagicMirror configuration:

```javascript
{
  module: "MMM-AnkerDeviceStatus",
  position: "top_right",
  config: {
    title: "Anker Power",
    showOffline: true,
    showBattery: true,
    showPower: true,
    compact: false
  }
}
```

Restart MagicMirror after changing credentials or polling settings.

## Display configuration

| Option | Default | Description |
| --- | --- | --- |
| `title` | `"Anker Power"` | Heading; empty string hides it |
| `showOffline` | `true` | Include explicitly offline devices; unknown devices are retained |
| `deviceIds` | `[]` | Serial-number allowlist; empty shows all account devices |
| `deviceNames` | `{}` | Name overrides keyed by device serial number |
| `showBattery` | `true` | Battery reading/bar for battery-equipped devices |
| `showPower` | `true` | Input/output and available solar watts |
| `showTemperature` | `false` | Temperature in Celsius when provided by MQTT |
| `showUpdated` | `true` | Age of the last successful HTTP cloud poll |
| `compact` | `false` | Smaller cards |

Example: `deviceNames: { "YOUR_DEVICE_SERIAL": "Office backup" }`.
Find serial numbers in the Anker app/device label. Names are rendered as text,
not HTML. Multiple module instances share one account connection.

## Server configuration

Set these in `.env` or MagicMirror's process environment:

| Variable | Default | Description |
| --- | --- | --- |
| `ANKER_EMAIL` | Required | Anker account email |
| `ANKER_PASSWORD` | Required | Anker account password |
| `ANKER_COUNTRY` | Required | Two-letter account country code, such as `US` or `DE` |
| `ANKER_POLL_INTERVAL` | `300` | HTTP polling seconds, minimum 60 |
| `ANKER_DETAILS_INTERVAL` | `1800` | Device discovery/detail polling seconds, at least the HTTP interval |
| `ANKER_MQTT` | `true` | Enable passive MQTT; `true` or `false` |
| `ANKER_MQTT_STALE_SECONDS` | `600` | Stop using MQTT readings after this many seconds without a decoded device update |
| `ANKER_PYTHON` | Module's `.venv` | Interpreter path; **process environment only** |

MQTT-backed cards refresh about every 15 seconds; that does not increase HTTP
polling. Cloud data is often already delayed by Anker. A fetched timestamp
indicates a successful cloud poll, **not** a fresh measurement from every device.
Expired/disconnected MQTT readings fall back to available HTTP values, otherwise
show `--`. A partial AC/DC reading is not shown as total input/output. Zero watts
and zero battery are preserved. During an HTTP failure the last successful
cards remain visible with an error and their cloud-poll age.

## Troubleshooting

- **Cannot start helper:** Create `.venv` on the mirror host and install the
  requirements, or set `ANKER_PYTHON` in the launch environment.
- **Missing dependencies:** Run the pip installation again in the interpreter
  actually used by the helper. The requirements explicitly include runtime
  dependencies missing from the upstream wheel metadata.
- **Cloud poll failed:** Check account password/country, internet connection and
  Anker service availability. Errors intentionally omit raw API responses and
  credentials. Cloud polls retry at the configured interval; helper crashes
  retry after 60 seconds.
- **Waiting for MQTT / MQTT stale:** The device has not supplied a recent
  decoded MQTT update. Confirm Wi-Fi connectivity and model support; wake the
  device or open its app to see whether it starts publishing.
- **No devices:** Verify devices appear in the Anker app for this account.
  Shared accounts have limited device-detail permissions.
- **macOS certificate failures:** Install the root certificates supplied with
  your Python installation. Do not disable TLS verification.

## Validation

No npm dependencies are needed for this module's tests:

```sh
npm run check
npm test
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Tests cover normalization, missing/zero/stale values, polling/MQTT setup,
rendering, filtering, instance isolation, shared workers and failure recovery.
Real account/device compatibility must be checked with your own account.
