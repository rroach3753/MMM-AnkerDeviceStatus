# Changelog

Notable changes to MMM-AnkerDeviceStatus are documented here.

## [Unreleased]

## [1.1.0] - 2026-10-18

### Added

- Add configurable firmware update indicators to device headers.
- Add CI for JavaScript lint/tests and Python 3.12, 3.13, and 3.14.

### Fixed

- Validate device records, timestamps, and warnings at the Python worker boundary
  while preserving last-known-good readings on invalid output.
- Consolidate frontend model checks and clear rendering timers on stop.

### Changed

- Refresh installation, configuration, updating, and troubleshooting guidance.
- Raise minimum runtime dependency versions without changing supported Python
  versions and declare the MIT license in package metadata.

### Security

- Protect the shared server-owned monitoring worker from browser unsubscribe
  messages and bound retained subscription IDs.

## [1.0.0] - 2026-10-09

- Initial stable release.
- Add Anker account discovery and read-only MQTT telemetry.
- Display SOLIX device battery, power, temperature, charging state, and
  device-reported runtime while discharging.
- Add configurable device filters, custom names, and compact/two-column display.
- Add automated JavaScript and Python tests.
