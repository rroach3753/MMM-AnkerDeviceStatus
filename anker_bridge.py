"""Read-only cloud polling and passive MQTT telemetry for MagicMirror."""

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
    telemetry: str


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
) -> Device:
    fresh = mqtt_connected and mqtt_age is not None and mqtt_age <= stale_seconds
    mqtt = raw.get("mqtt_data") or {}
    values = mqtt if fresh else {}
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
        "telemetry": (
            "Cloud only (MQTT disabled)" if not mqtt_enabled else
            "MQTT" if fresh else "MQTT stale" if mqtt_age is not None else
            "Waiting for MQTT" if raw.get("mqtt_supported") else "Cloud only"
        ),
    }


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
    mqtt_seen: dict[str, float] = {}
    devices: list[Device] = []
    fetched_at: str | None = None
    cloud_error: str | None = None
    warning: str | None = None
    expected_topics: set[str] = set()
    next_poll = next_details = 0.0
    loop = asyncio.get_running_loop()

    def mqtt_updated(serial: str) -> None:
        loop.call_soon_threadsafe(mqtt_seen.__setitem__, serial, time.monotonic())

    async with ClientSession(timeout=ClientTimeout(total=45)) as session:
        client = AnkerSolixApi(email, password, country, session, logger)
        client.mqtt_update_callback(mqtt_updated)
        try:
            while True:
                now = time.monotonic()
                if now >= next_poll:
                    try:
                        async with asyncio.timeout(105):
                            await client.update_sites()
                            if now >= next_details:
                                await client.update_device_details()
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
                                mqtt_session = await client.startMqttSession()
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
                if cloud_error is None:
                    now = time.monotonic()
                    devices = [
                        normalize_device(
                            serial, raw, client.sites,
                            now - mqtt_seen[serial] if serial in mqtt_seen else None,
                            connected, stale_seconds, mqtt_enabled,
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
