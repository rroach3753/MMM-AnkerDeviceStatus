import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from anker_bridge import Telemetry, interval, monitor, normalize_device, number, power_total, subscribe_devices


class NormalizationTests(unittest.TestCase):
    def device(self, raw: dict, age: float | None = None, connected: bool = True):
        return normalize_device("serial", raw, {}, age, connected, 600)

    def test_zero_is_not_missing(self):
        device = self.device({"battery_soc": "0", "input_power": "0", "output_power": 0})
        self.assertEqual((device["battery"], device["input"], device["output"]), (0, 0, 0))

    def test_missing_and_invalid_values_are_not_zero(self):
        for value in (None, "", "unknown", "NaN", "Infinity", True, [], {}):
            self.assertIsNone(number(value))
        device = self.device({"battery_soc": "120"})
        self.assertIsNone(device["battery"])
        self.assertIsNone(device["online"])
        self.assertIsNone(device["input"])

    def test_fresh_mqtt_takes_precedence(self):
        device = self.device({
            "status_desc": "offline",
            "battery_soc": "80",
            "mqtt_data": {"battery_soc": "0", "input_power": "25", "output_power": "0"},
        }, 5)
        self.assertEqual(device["battery"], 0)
        self.assertEqual(device["input"], 25)
        self.assertTrue(device["online"])
        self.assertEqual(device["telemetry"], "MQTT")

    def test_stale_or_disconnected_mqtt_is_not_used(self):
        raw = {"battery_soc": "80", "mqtt_data": {"battery_soc": "90", "input_power": "45"}}
        for age, connected in ((601, True), (5, False)):
            device = self.device(raw, age, connected)
            self.assertEqual(device["battery"], 80)
            self.assertIsNone(device["input"])
            self.assertEqual(device["telemetry"], "MQTT stale")

    def test_sums_only_complete_power_components(self):
        self.assertEqual(power_total({"ac_output_power": 20, "dc_output_power": 0}, "output"), 20)
        self.assertIsNone(power_total({"ac_output_power": 20}, "output"))
        self.assertEqual(power_total({"output_power": 0, "ac_output_power": 20}, "output"), 0)

    def test_names_sites_and_offline(self):
        device = normalize_device("serial", {
            "alias": "<b>Office</b>", "device_pn": "A1761", "type": "pps",
            "site_id": "site", "status_desc": "offline",
        }, {"site": {"site_info": {"site_name": "Home"}}}, None, False, 600)
        self.assertEqual(device["name"], "<b>Office</b>")
        self.assertEqual(device["site"], "Home")
        self.assertFalse(device["online"])

    def test_firmware_update_state_is_explicit_and_unknown_when_missing(self):
        for raw_value, expected in ((True, True), (False, False), (None, None), ("false", None), (1, None)):
            with self.subTest(raw_value=raw_value):
                device = self.device({"is_ota_update": raw_value})
                self.assertEqual(device["firmwareUpdate"], expected)

    def test_interval_validation(self):
        for value in ("abc", "0", "59"):
            with patch.dict(os.environ, {"ANKER_POLL_INTERVAL": value}):
                with self.assertRaises(ValueError):
                    interval("ANKER_POLL_INTERVAL", 300, 60)

    def test_runtime_requires_fresh_discharge_evidence(self):
        cases = [
            ("AS220", {"battery_status": 1, "remaining_time_hours": 2.5}, True, 2.5),
            ("AS220", {"battery_status": 2, "remaining_time_hours": 2.5}, False, None),
            ("AS220", {"battery_status": 0, "remaining_time_hours": 2.5}, False, None),
            ("AS220", {"remaining_time_hours": 2.5}, None, None),
            ("AS220", {"battery_status": 1, "remaining_time_hours": -1}, True, None),
            ("A1761", {"charging_status": 0, "ac_input_power": 0, "dc_input_power": 0,
                        "output_power_total": 100, "remaining_time_hours": 0}, True, 0),
            ("A1761", {"charging_status": 0, "ac_input_power": 0,
                        "output_power_total": 100, "remaining_time_hours": 2}, None, None),
            ("A1761", {"charging_status": 0, "input_power": 100,
                        "output_power_total": 100, "remaining_time_hours": 2}, False, None),
        ]
        for model, values, discharge, runtime in cases:
            with self.subTest(model=model, values=values):
                device = normalize_device("sn", {"device_pn": model}, {}, 0, True, 60, True, values)
                self.assertEqual(device["discharging"], discharge)
                self.assertEqual(device["runtimeHours"], runtime)
                stale = normalize_device("sn", {"device_pn": model}, {}, 61, True, 60, True, values)
                self.assertIsNone(stale["runtimeHours"])
                self.assertIsNone(stale["discharging"])

    def test_disabled_mqtt_is_explicit(self):
        device = normalize_device("serial", {"mqtt_supported": True}, {}, None, False, 600, False)
        self.assertEqual(device["telemetry"], "Cloud only (MQTT disabled)")

    def test_subscribes_device_topics_without_publishing(self):
        class Session:
            def __init__(self):
                self.subscriptions = {"old/#"}
                self.calls = []

            def get_topic_prefix(self, deviceDict):
                return f"dt/app/{deviceDict['device_pn']}/{deviceDict['device_sn']}/"

            def subscribe(self, topic):
                self.calls.append(("subscribe", topic))
                self.subscriptions.add(topic)
                return 0

            def unsubscribe(self, topic):
                self.calls.append(("unsubscribe", topic))
                self.subscriptions.remove(topic)
                return 0

        session = Session()
        devices = {"sn": {"device_sn": "sn", "device_pn": "A1761", "mqtt_supported": True}}
        self.assertEqual(subscribe_devices(session, devices), {"dt/app/A1761/sn/#"})
        self.assertEqual(session.calls, [("unsubscribe", "old/#"), ("subscribe", "dt/app/A1761/sn/#")])
        subscribe_devices(session, devices)
        self.assertEqual(len(session.calls), 2)


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_requests_are_throttled(self):
        controller = type("Controller", (), {
            "controls": {"realtime_trigger": {}},
            "realtime_trigger": AsyncMock(return_value={}),
            "status_request": AsyncMock(return_value={}),
        })()
        telemetry = Telemetry(None, asyncio.get_running_loop(), 600)
        telemetry.controllers = {"serial": controller}
        await telemetry.request(1000)
        await telemetry.request(1015)
        controller.realtime_trigger.assert_awaited_once_with(timeout=300)
        controller.status_request.assert_awaited_once()
        await telemetry.request(1060)
        self.assertEqual(controller.status_request.await_count, 2)
        await telemetry.request(1240)
        self.assertEqual(controller.realtime_trigger.await_count, 2)

    async def test_field_freshness_and_model_specific_charging(self):
        telemetry = Telemetry(None, asyncio.get_running_loop(), 60)
        telemetry.record("station", {"battery_soc": 80, "temperature": 25, "charging_status": 1}, 100)
        telemetry.record("station", {"input_power": 0}, 150)
        current = telemetry.current("station", 170)
        self.assertEqual(current, {"input_power": 0})
        device = normalize_device("station", {
            "device_pn": "A1761", "mqtt_data": {"battery_soc": 80, "charging_status": 1},
        }, {}, 20, True, 60, True, current)
        self.assertIsNone(device["battery"])
        self.assertIsNone(device["temperature"])
        self.assertIsNone(device["charging"])
        for model, field, state, expected in (
            ("A1761", "charging_status", 0, False),
            ("A1761", "charging_status", 3, True),
            ("AS220", "battery_status", 1, False),
            ("AS220", "battery_status", 2, True),
            ("AS220", "battery_status", 4, None),
        ):
            device = normalize_device("station", {"device_pn": model}, {}, 0, True, 60, True, {field: state})
            self.assertEqual(device["charging"], expected)

    async def test_poll_discovery_mqtt_and_cleanup(self):
        class Client:
            def __init__(self, *args):
                self.devices = {"serial": {"name": "Station", "type": "pps", "battery_soc": "50"}}
                self.sites = {}
                self.mqttsession = None
                self.update_sites = AsyncMock()
                self.update_device_details = AsyncMock()
                self.startMqttSession = AsyncMock(return_value=None)
                self.stopped = False

            def mqtt_update_callback(self, callback):
                self.callback = callback

            def stopMqttSession(self):
                self.stopped = True

        client = Client()
        output = io.StringIO()
        with (
            patch.dict(os.environ, {
                "ANKER_EMAIL": "test@example.com", "ANKER_PASSWORD": "test",
                "ANKER_COUNTRY": "US", "ANKER_MQTT": "true",
            }),
            patch("anker_solix_api.api.AnkerSolixApi", return_value=client),
            patch("anker_bridge.asyncio.sleep", side_effect=asyncio.CancelledError),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await monitor()
        state = json.loads(output.getvalue())
        self.assertEqual(state["devices"][0]["battery"], 50)
        self.assertIsNotNone(state["fetchedAt"])
        self.assertIsNone(state["error"])
        self.assertIn("MQTT connection failed", state["warning"])
        client.update_sites.assert_awaited_once()
        client.update_device_details.assert_awaited_once()
        client.startMqttSession.assert_awaited_once()
        self.assertTrue(client.stopped)


if __name__ == "__main__":
    unittest.main()
