"""Read-only cloud polling and requested MQTT telemetry for MagicMirror."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from anker_solix_api.mqtt import AnkerSolixMqttSession


class ChargerDetail(TypedDict):
    key: str
    label: str
    group: str
    value: str | float
    unit: str


class Device(TypedDict):
    id: str
    name: str
    model: str
    type: str
    site: str
    online: bool | None
    status: str
    battery: float | None
    input: float | None
    output: float | None
    solar: float | None
    temperature: float | None
    charging: bool | None
    discharging: bool | None
    runtimeHours: float | None
    chargeTimeHours: float | None
    firmwareUpdate: bool | None
    telemetry: str
    chargerDetails: list[ChargerDetail]


CHARGER_MODELS = {"A1903", "A2345", "A25X7", "A2687", "A91B2"}


def charger_reported_values(model: str, values: dict) -> dict:
    """Translate port events without refreshing unrelated cached readings."""
    reported = dict(values)
    if model not in CHARGER_MODELS:
        return reported
    ports = (["ac_1", "ac_2", "usbc_1", "usbc_2", "usbc_3", "usbc_4"]
             if model == "A91B2" else ["usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba"])
    for selector, value_key, suffix in [
        ("set_port_switch_select", "set_port_switch", "switch"),
        ("set_port_timer_select", "set_port_timer_switch", "timer_switch"),
        ("set_ac_port_switch_select", "set_ac_port_switch", "switch"),
    ]:
        port_id = number(values.get(selector))
        event_ports = ["ac_1", "ac_2"] if selector == "set_ac_port_switch_select" else ports
        if port_id is None or not port_id.is_integer() or not 0 <= port_id < len(event_ports):
            continue
        port = event_ports[int(port_id)]
        if value_key in values:
            reported[f"{port}_{suffix}"] = values[value_key]
        if selector == "set_port_timer_select":
            for key in ("port_timer_seconds", "port_timer_remaining_seconds"):
                if key in values:
                    reported[f"{port}_{key.removeprefix('port_')}"] = values[key]
    return reported


def charger_details(model: str, values: dict) -> list[ChargerDetail]:
    """Expose only known decoded fields, never arbitrary MQTT payload data."""
    if model not in CHARGER_MODELS:
        return []
    details: list[ChargerDetail] = []

    def add(key: str, label: str, group: str, unit: str = "", choices: dict | None = None) -> None:
        raw = values.get(key)
        if raw is None:
            return
        if key.endswith("_protocols"):
            protocols = ["scp", "ufcs", "pd12v", "pps11v", "pps16v", "pps20v", "huawei", "xiaomi"]
            if isinstance(raw, (bytes, bytearray)) and len(raw) == 1:
                value = ", ".join(name for bit, name in enumerate(protocols) if raw[0] & (1 << bit)) or "None"
            elif isinstance(raw, list) and all(isinstance(item, str) and item in protocols for item in raw):
                value = ", ".join(raw) or "None"
            else:
                return
        elif key == "sw_version":
            if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
                return
            if not isinstance(raw, str) and (not math.isfinite(raw) or raw < 0):
                return
            value = str(raw)[:128]
        else:
            value = number(raw)
            if value is None or value < 0:
                return
            if key.endswith("_weekdays"):
                if not value.is_integer() or value > 127:
                    return
                value = ", ".join(
                    day for bit, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
                    if int(value) & (1 << bit)
                ) or "None"
            if choices is not None:
                value = choices.get(value, f"Unknown ({value:g})")
        details.append({"key": key, "label": label, "group": group, "value": value, "unit": unit})

    ports = ["usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba_1", "usba_2", "usba"]
    switches = {0: "Off", 1: "On"}
    for port in ports:
        label = "USB-A (shared)" if port == "usba" else port.replace("usbc_", "USB-C ").replace("usba_", "USB-A ")
        for suffix, title, group, unit, choices in [
            ("power", "Power", "power", " W", None),
            ("voltage", "Voltage", "voltage", " V", None),
            ("current", "Current", "current", " A", None),
            ("status", "Activity", "activity", "", {0: "Inactive", 1: "Active"}),
            ("switch", "Switch", "switches", "", switches),
            ("priority", "Priority", "priority", "", {1: "Normal", 2: "Prioritized"}),
            ("timer_switch", "Timer", "timers", "", switches),
            ("timer_seconds", "Timer duration", "timers", " s", None),
            ("timer_remaining_seconds", "Timer remaining", "timers", " s", None),
        ]:
            add(f"{port}_{suffix}", f"{label} {title}", group, unit, choices)
        for boundary in ("start", "end"):
            for suffix, unit, choices in [
                ("switch", "", switches), ("hour", " h", None),
                ("minute", " min", None), ("weekdays", "", None),
            ]:
                add(f"{port}_{boundary}_{suffix}", f"{label} {boundary} {suffix}", "schedules", unit, choices)
    for port in ("ac_1", "ac_2"):
        add(f"{port}_switch", port.replace("ac_", "AC outlet ") + " switch", "acOutlets", choices=switches)
    for key, label, group, choices, unit in [
        ("usage_mode", "Charging mode", "mode", {
            1: "AI Power", 2: "Connection priority", 3: "Dual laptop", 4: "Low power", 5: "Custom"
        }, ""),
        ("custom_profile_number", "Custom profile", "allocation", None, ""),
        ("auto_exit_switch", "Auto-exit custom mode", "allocation", switches, ""),
        ("display_brightness", "Display brightness", "display", None, "%"),
        ("display_timeout_mode", "Display timeout", "display", {
            0: "Never", 1: "30 seconds", 2: "1 minute", 3: "5 minutes", 4: "30 minutes"
        }, ""),
        ("theme_id", "Theme ID", "display", None, ""),
        ("theme_type", "Theme type", "display", None, ""),
        ("custom_theme_active", "Custom theme", "display", switches, ""),
        ("stock_theme_active", "Stock theme", "display", switches, ""),
        ("knob_mode", "Knob direction", "display", {0: "Forward", 1: "Backward"}, ""),
        ("clock_mode", "Clock format", "clock", {0: "12-hour", 1: "24-hour"}, ""),
        ("clock_switch", "Clock", "clock", switches, ""),
        ("holiday_switch", "Weekend mode", "clock", switches, ""),
        ("clock_display_start_hour", "Clock start hour", "clock", None, " h"),
        ("clock_display_start_minute", "Clock start minute", "clock", None, " min"),
        ("clock_display_end_hour", "Clock end hour", "clock", None, " h"),
        ("clock_display_end_minute", "Clock end minute", "clock", None, " min"),
        ("clock_display_weekdays", "Clock weekdays", "clock", None, ""),
        ("sw_version", "Software version", "software", None, ""),
    ]:
        add(key, label, group, unit, choices)
    for port in ("c1", "c2", "c3", "c4", "a"):
        add(f"custom_usb_{port}_power_limit", f"Custom USB {port} power limit", "allocation", " W")
        if port != "a":
            add(f"custom_usb_{port}_protocols", f"Custom USB {port} protocols", "allocation")
    return details


def number(value: object) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(str(value))
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def first_number(data: dict, *keys: str) -> float | None:
    for key in keys:
        value = number(data.get(key))
        if value is not None:
            return value
    return None


def power_total(data: dict, direction: str) -> float | None:
    total = first_number(data, f"{direction}_power", f"{direction}_power_total")
    if total is not None:
        return total
    ac = first_number(data, f"ac_{direction}_power", f"ac_{direction}_power_total")
    dc = first_number(data, f"dc_{direction}_power", f"dc_{direction}_power_total")
    # A missing component is not zero; don't label a partial reading as a total.
    return ac + dc if ac is not None and dc is not None else None


def subscribe_devices(session: AnkerSolixMqttSession, devices: dict) -> set[str]:
    topics: set[str] = set()
    for raw in list(devices.values()):
        if raw.get("mqtt_supported"):
            prefix = session.get_topic_prefix(deviceDict=raw)
            if not prefix:
                raise RuntimeError("Missing MQTT device topic")
            topics.add(f"{prefix}#")
    for topic in session.subscriptions.copy() - topics:
        if session.unsubscribe(topic) not in (None, 0):
            raise RuntimeError("MQTT unsubscribe failed")
    for topic in topics - session.subscriptions.copy():
        if session.subscribe(topic) != 0:
            raise RuntimeError("MQTT subscribe failed")
    return topics


def normalize_device(
    serial: str,
    raw: dict,
    sites: dict,
    mqtt_age: float | None,
    mqtt_connected: bool,
    stale_seconds: int,
    mqtt_enabled: bool = True,
    reported_values: dict | None = None,
) -> Device:
    fresh = mqtt_connected and mqtt_age is not None and mqtt_age <= stale_seconds
    mqtt = raw.get("mqtt_data") or {}
    values = (mqtt if reported_values is None else reported_values) if fresh else {}
    battery = first_number(values, "battery_soc", "battery_soc_total", "main_battery_soc")
    if battery is None:
        battery = first_number(raw, "battery_soc")
    if battery is not None and not 0 <= battery <= 100:
        battery = None
    input_power = power_total(values, "input")
    output_power = power_total(values, "output")
    if input_power is None:
        input_power = first_number(raw, "input_power")
    if output_power is None:
        output_power = first_number(raw, "output_power")
    description = str(raw.get("status_desc") or "unknown").lower()
    online = {"online": True, "offline": False}.get(description)
    if fresh:
        online = True
    site = sites.get(raw.get("site_id"), {})
    charging = None
    discharging = None
    model = raw.get("device_pn")
    if model == "AS220":
        state = first_number(values, "battery_status")
        if state in (0, 1, 2):
            charging = state == 2
            discharging = state == 1
    elif model == "A1761":
        state = first_number(values, "charging_status")
        mqtt_input = power_total(values, "input")
        mqtt_output = power_total(values, "output")
        if state in (0, 1, 2, 3):
            # This field identifies the input source, including AC bypass.
            if state == 0:
                charging = False
            if mqtt_input is not None and mqtt_output is not None:
                charging = state > 0 and mqtt_input > mqtt_output
                discharging = mqtt_output > mqtt_input
    remaining = first_number(values, "remaining_time_hours")
    # 0xffff scaled by 0.1 is reported when no time estimate is available.
    if remaining is not None and not 0 <= remaining < 6553.5:
        remaining = None
    return {
        "id": serial,
        "name": str(raw.get("alias") or raw.get("name") or raw.get("device_name") or serial),
        "model": str(raw.get("device_pn") or ""),
        "type": str(raw.get("type") or "device"),
        "site": str((site.get("site_info") or {}).get("site_name") or ""),
        "online": online,
        "status": str(raw.get("charging_status_desc") or description).replace("_", " "),
        "battery": battery,
        "input": input_power,
        "output": output_power,
        "solar": first_number(values, "pv_power", "pv_power_total"),
        "temperature": first_number(values, "temperature"),
        "charging": charging,
        "discharging": discharging,
        "runtimeHours": remaining if discharging is True else None,
        "chargeTimeHours": remaining if charging is True else None,
        "firmwareUpdate": (
            raw["is_ota_update"] if isinstance(raw.get("is_ota_update"), bool) else None
        ),
        "telemetry": (
            "Cloud only (MQTT disabled)" if not mqtt_enabled else
            "MQTT" if fresh else "MQTT stale" if mqtt_age is not None else
            "Waiting for MQTT" if raw.get("mqtt_supported") else "Cloud only"
        ),
        "chargerDetails": charger_details(str(model or ""), values),
    }


class Telemetry:
    """Keep individual field receipt times, not freshness of accumulated caches."""

    REQUEST_MODELS = {"A1761", "AS220", "A2345", "A91B2"}

    def __init__(self, client, loop, stale_seconds: int) -> None:
        self.client = client
        self.loop = loop
        self.stale_seconds = stale_seconds
        self.reported: dict[str, dict[str, tuple[object, float]]] = {}
        self.seen: dict[str, float] = {}
        self.controllers: dict = {}
        self.last_trigger = 0.0
        self.last_status = 0.0
        self.callback_error: str | None = None

    def receive(self, session, topic, message, data, model, deviceSn, extracted_values, *args, **kwargs) -> None:
        try:
            self.client.mqtt_received(session, topic, message, data, model, deviceSn, extracted_values, *args, **kwargs)
            self.loop.call_soon_threadsafe(
                self.record, deviceSn, charger_reported_values(model, extracted_values), time.monotonic()
            )
        except Exception as error:
            self.loop.call_soon_threadsafe(
                setattr, self, "callback_error", f"MQTT decoding failed ({type(error).__name__})."
            )

    def record(self, serial: str, values: dict, received_at: float) -> None:
        self.seen[serial] = received_at
        fields = self.reported.setdefault(serial, {})
        for key, value in values.items():
            fields[key] = (value, received_at)

    def current(self, serial: str, now: float) -> dict:
        return {
            key: value for key, (value, received_at) in self.reported.get(serial, {}).items()
            if 0 <= now - received_at <= self.stale_seconds
        }

    def discover(self) -> None:
        from anker_solix_api.mqtt_factory import SolixMqttDeviceFactory

        active = set(self.client.devices)
        self.controllers = {sn: controller for sn, controller in self.controllers.items() if sn in active}
        for serial, device in list(self.client.devices.items()):
            if device.get("device_pn") in self.REQUEST_MODELS and serial not in self.controllers:
                controller = SolixMqttDeviceFactory(self.client, serial).create_device()
                if controller is None:
                    raise RuntimeError("Supported MQTT controller unavailable")
                self.controllers[serial] = controller
                self.last_trigger = self.last_status = 0.0

    async def request(self, now: float) -> None:
        if now - self.last_trigger >= 240:
            for controller in self.controllers.values():
                if "realtime_trigger" in controller.controls:
                    if await controller.realtime_trigger(timeout=300) is None:
                        raise RuntimeError("MQTT telemetry trigger failed")
            self.last_trigger = now
        if now - self.last_status >= min(60, self.stale_seconds / 2):
            for controller in self.controllers.values():
                if await controller.status_request() is None:
                    raise RuntimeError("MQTT status request failed")
            self.last_status = now


def emit(payload: dict) -> None:
    print(json.dumps(payload, allow_nan=False), flush=True)


def interval(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum} seconds.")
    return value


async def monitor() -> None:
    from aiohttp import ClientSession, ClientTimeout
    from anker_solix_api.api import AnkerSolixApi
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"), override=False)
    email = os.environ.get("ANKER_EMAIL", "").strip()
    password = os.environ.get("ANKER_PASSWORD", "")
    country = os.environ.get("ANKER_COUNTRY", "").strip().upper()
    if not email or not password or len(country) != 2 or not country.isalpha():
        raise ValueError("Set ANKER_EMAIL, ANKER_PASSWORD and a two-letter ANKER_COUNTRY in the module's .env.")
    poll_interval = interval("ANKER_POLL_INTERVAL", 300, 60)
    details_interval = interval("ANKER_DETAILS_INTERVAL", 1800, poll_interval)
    stale_seconds = interval("ANKER_MQTT_STALE_SECONDS", 600, 60)
    mqtt_setting = os.environ.get("ANKER_MQTT", "true").lower()
    if mqtt_setting not in ("true", "false"):
        raise ValueError("ANKER_MQTT must be true or false.")
    mqtt_enabled = mqtt_setting == "true"
    logger = logging.getLogger("anker_bridge")
    # Upstream debug logs can contain account data; only sanitized errors go to the UI.
    logger.disabled = True
    logging.disable(logging.CRITICAL)
    devices: list[Device] = []
    fetched_at: str | None = None
    cloud_error: str | None = None
    warning: str | None = None
    expected_topics: set[str] = set()
    next_poll = next_details = 0.0
    loop = asyncio.get_running_loop()

    async with ClientSession(timeout=ClientTimeout(total=45)) as session:
        client = AnkerSolixApi(email, password, country, session, logger)
        telemetry = Telemetry(client, loop, stale_seconds)
        try:
            while True:
                now = time.monotonic()
                if now >= next_poll:
                    try:
                        async with asyncio.timeout(105):
                            await client.update_sites()
                            if now >= next_details:
                                await client.update_device_details()
                                if mqtt_enabled:
                                    telemetry.discover()
                                next_details = time.monotonic() + details_interval
                        fetched_at = datetime.now(timezone.utc).isoformat()
                        cloud_error = None
                    except Exception as error:
                        # Process boundary: preserve last good data, never serialize raw API errors.
                        cloud_error = (
                            f"Anker cloud poll failed ({type(error).__name__}). "
                            "Check account credentials, country, network and API availability."
                        )
                    if mqtt_enabled and cloud_error is None:
                        try:
                            async with asyncio.timeout(30):
                                mqtt_session = await client.startMqttSession(message_callback=telemetry.receive)
                                if mqtt_session:
                                    expected_topics = subscribe_devices(mqtt_session, client.devices)
                            warning = None if mqtt_session else "MQTT connection failed; only cloud readings are available."
                        except Exception as error:
                            warning = f"MQTT unavailable ({type(error).__name__}); only cloud readings are available."
                    next_poll = time.monotonic() + poll_interval
                connected = bool(client.mqttsession and client.mqttsession.is_connected())
                if mqtt_enabled and not connected and warning is None:
                    warning = "MQTT disconnected; reconnecting on the next cloud poll."
                elif connected and client.mqttsession and expected_topics - client.mqttsession.subscriptions.copy():
                    warning = "Waiting for MQTT subscription acknowledgements; check account permissions if this persists."
                elif connected and warning and warning.startswith("Waiting for MQTT"):
                    warning = None
                if connected and cloud_error is None:
                    try:
                        async with asyncio.timeout(30):
                            await telemetry.request(time.monotonic())
                        if warning and warning.startswith("MQTT telemetry request failed"):
                            warning = None
                    except Exception as error:
                        warning = f"MQTT telemetry request failed ({type(error).__name__}); retrying in 60 seconds."
                        telemetry.last_status = time.monotonic()
                        telemetry.last_trigger = telemetry.last_status - 180
                if telemetry.callback_error:
                    warning = telemetry.callback_error
                if cloud_error is None:
                    now = time.monotonic()
                    devices = [
                        normalize_device(
                            serial, raw, client.sites,
                            now - telemetry.seen[serial] if serial in telemetry.seen else None,
                            connected, stale_seconds, mqtt_enabled,
                            telemetry.current(serial, now),
                        )
                        for serial, raw in list(client.devices.items())
                    ]
                emit({
                    "devices": devices,
                    "fetchedAt": fetched_at,
                    "error": cloud_error,
                    "warning": warning,
                })
                await asyncio.sleep(min(15, max(0.1, next_poll - time.monotonic())))
        finally:
            client.stopMqttSession()


if __name__ == "__main__":
    os.umask(0o077)
    try:
        asyncio.run(monitor())
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    except ImportError:
        emit({"devices": [], "fetchedAt": None, "error": "Python dependencies missing. Install requirements.txt in the module's Python environment."})
        sys.exit(1)
    except ValueError as error:
        emit({"devices": [], "fetchedAt": None, "error": str(error)})
        sys.exit(1)
    except Exception as error:
        emit({"devices": [], "fetchedAt": None, "error": f"Anker helper failed ({type(error).__name__}). Check the Python environment and module setup."})
        sys.exit(1)
