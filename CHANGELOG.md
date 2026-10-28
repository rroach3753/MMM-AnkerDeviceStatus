# Changelog

<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

Notable changes to MMM-AnkerDeviceStatus are documented here.

## [Unreleased]

### Changed

- Default to three device cards per row in `center_*` positions and two
  elsewhere; add `cardsPerRow` to override the layout.

### Changed

- Hide charger USB ports without positive measured output power by default;
  set `showInactiveChargerPorts: true` to include them. SOLIX is unchanged.
- Show only per-port power by default on charger/station cards; other detail
  groups remain opt-in through `chargerGroups`.
- Hide unavailable whole-device Input/Output rows on charger/station cards,
  without changing SOLIX metrics or behavior.

## [1.3.0] - 2026-10-27

### Added

- Show supported Anker charger/station cards and available per-port telemetry,
  schedules, timers, priorities, charging modes, allocation, display/clock
  settings, software version and decoded AC outlet states by default.
- Add `showChargers`, `showChargerDetails`, `chargerGroups` and `chargerFields`
  display controls. Charger details remain read-only and use field-level MQTT
  freshness; unavailable readings are not fabricated.

### Changed

- Include supported charger/station cards by default; set `showChargers: false`
  to restore the previous SOLIX-only view.
- Format time-to-full estimates longer than 24 hours as days, hours, and minutes.

### Fixed

- Do not label C1000 AC pass-through as battery charging; require fresh net
  charging power and retain unknown state when power readings are incomplete.
- Reject the unavailable remaining-time value of 6553.5 hours.
- Use npm script commands in CI so checks also run on Node.js 20.

## [1.2.0] - 2026-10-19

### Added

- Show device-reported time to full while C1000 and S2000 devices are charging.
- Add the `showChargeTime` display option, enabled by default, and validate
  charge-time readings at the worker boundary.

### Changed

- Share hours/minutes formatting between discharge runtime and time to full.
- Document missing/stale estimates and the need for live charging verification.

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
