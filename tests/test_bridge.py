import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from anker_bridge import Telemetry, charger_details, charger_reported_values, interval, monitor, normalize_device, number, power_total, subscribe_devices


class NormalizationTests(unittest.TestCase):
    def test_charger_details_preserve_port_precision_and_zero(self):
        fields = charger_details("A2345", {
            "usbc_1_power": 0, "usbc_1_voltage": 20.125, "usbc_1_current": 2.45,
            "usbc_1_status": 0, "usbc_1_switch": 1, "usbc_1_priority": 2,
            "usba_2_power": "4.25", "account_token": "private",
        })
        by_key = {field["key"]: field for field in fields}
        self.assertEqual(by_key["usbc_1_power"]["value"], 0)
        self.assertEqual(by_key["usbc_1_voltage"]["value"], 20.125)
        self.assertEqual(by_key["usbc_1_current"]["value"], 2.45)
        self.assertEqual(by_key["usbc_1_status"]["value"], "Inactive")
        self.assertEqual(by_key["usbc_1_switch"]["value"], "On")
        self.assertEqual(by_key["usbc_1_priority"]["value"], "Prioritized")
        self.assertNotIn("account_token", by_key)

    def test_all_charger_groups_are_allowlisted_and_serializable(self):
        values = {
            "usbc_1_power": 40, "usbc_1_voltage": 20, "usbc_1_current": 2,
            "usbc_1_status": 1, "usbc_1_switch": 0, "usbc_1_priority": 1,
            "usbc_1_start_hour": 8, "usbc_1_start_weekdays": 62,
            "usbc_1_timer_remaining_seconds": 0, "ac_1_switch": 0,
            "usage_mode": 5, "custom_usb_c1_power_limit": 100,
            "custom_usb_c1_protocols": bytes([3]), "display_brightness": 20,
            "clock_mode": 1, "sw_version": "1.2.3",
        }
        fields = charger_details("A91B2", values)
        self.assertEqual({field["group"] for field in fields}, {
            "power", "voltage", "current", "activity", "switches", "priority",
            "schedules", "timers", "acOutlets", "mode", "allocation", "display", "clock", "software",
        })
        encoded = json.loads(json.dumps(fields))
        by_key = {field["key"]: field["value"] for field in encoded}
        self.assertEqual(by_key["custom_usb_c1_protocols"], "scp, ufcs")
        self.assertEqual(by_key["ac_1_switch"], "Off")
        self.assertEqual(by_key["usage_mode"], "Custom")
        self.assertEqual(charger_details("A2345", {"custom_usb_c1_protocols": ["pps16v"]})[0]["value"], "pps16v")
        self.assertEqual(by_key["usbc_1_start_weekdays"], "Tue, Wed, Thu, Fri, Sat")
        self.assertEqual(charger_details("A2345", {"usba_switch": 0})[0]["value"], "Off")

    def test_switch_and_timer_events_retain_correct_port_identity(self):
        for model, selector, expected in [
            ("A2345", 0, "usbc_1_switch"),
            ("A2345", 4, "usba_switch"),
            ("A91B2", 0, "ac_1_switch"),
            ("A91B2", 1, "ac_2_switch"),
            ("A91B2", 2, "usbc_1_switch"),
        ]:
            source = {"set_port_switch_select": selector, "set_port_switch": 0}
            values = charger_reported_values(model, source)
            self.assertEqual(values[expected], 0)
            self.assertNotIn(expected, source)
            self.assertEqual(charger_details(model, values)[0]["key"], expected)
        values = charger_reported_values("A2345", {
            "set_port_timer_select": 4, "set_port_timer_switch": 1,
            "port_timer_remaining_seconds": 0,
        })
        self.assertEqual(values["usba_timer_switch"], 1)
        self.assertEqual(values["usba_timer_remaining_seconds"], 0)
        self.assertNotIn("usba_timer_seconds", values)
        self.assertNotIn("ac_1_switch", charger_reported_values("A91B2", {
            "set_port_switch_select": "bad", "set_port_switch": 0,
        }))

    def test_charger_details_require_fresh_fields_not_merged_cache(self):
        telemetry = Telemetry(None, None, 60)
        telemetry.record("sn", {"usbc_1_power": 50, "display_brightness": 80}, 100)
        telemetry.record("sn", {"usbc_1_power": 0}, 150)
        raw = {"device_pn": "A2345", "mqtt_data": {"display_brightness": 80}}
        device = normalize_device("sn", raw, {}, 20, True, 60, True, telemetry.current("sn", 170))
        self.assertEqual([field["key"] for field in device["chargerDetails"]], ["usbc_1_power"])
        self.assertEqual(device["chargerDetails"][0]["value"], 0)
        for age, connected in ((61, True), (0, False)):
            self.assertEqual(normalize_device("sn", raw, {}, age, connected, 60)["chargerDetails"], [])
        self.assertEqual(charger_details("A1761", {"usbc_1_power": 1}), [])
        for value in (True, [], {}, "bad", -1, float("nan"), float("inf")):
            self.assertEqual(charger_details("A2345", {"usbc_1_power": value}), [])

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

    def test_charge_time_requires_fresh_charging_and_remaining_time(self):
        for model, field, charging_state, idle_state in (
            ("AS220", "battery_status", 2, 0),
            ("A1761", "charging_status", 1, 0),
        ):
            for remaining, expected in ((1.5, 1.5), (0, 0), (-1, None),
                                        (None, None), ("NaN", None), ("Infinity", None),
                                        (6553.5, None)):
                with self.subTest(model=model, remaining=remaining):
                    values = {field: charging_state, "remaining_time_hours": remaining}
                    if model == "A1761":
                        values.update({"input_power_total": 500, "output_power_total": 100})
                    device = normalize_device("sn", {"device_pn": model}, {}, 0, True, 60, True, values)
                    self.assertEqual(device["chargeTimeHours"], expected)
                    self.assertIsNone(device["runtimeHours"])
                    for age, connected in ((61, True), (0, False)):
                        stale = normalize_device("sn", {"device_pn": model}, {}, age, connected, 60, True, values)
                        self.assertIsNone(stale["chargeTimeHours"])
            for values in (
                {field: idle_state, "remaining_time_hours": 2},
                {"remaining_time_hours": 2},
                {field: charging_state},
            ):
                device = normalize_device("sn", {"device_pn": model}, {}, 0, True, 60, True, values)
                self.assertIsNone(device["chargeTimeHours"])

    def test_c1000_input_source_is_not_battery_charging(self):
        for source in (1, 2, 3):
            with self.subTest(source=source):
                values = {"charging_status": source, "main_battery_soc": 100,
                          "ac_input_power": 158, "dc_input_power": 0,
                          "output_power_total": 158, "remaining_time_hours": 6553.5}
                device = normalize_device("sn", {"device_pn": "A1761"}, {}, 0, True, 60, True, values)
                self.assertFalse(device["charging"])
                self.assertFalse(device["discharging"])
                self.assertIsNone(device["chargeTimeHours"])
                values["output_power_total"] = 50
                values["remaining_time_hours"] = 1.5
                device = normalize_device("sn", {"device_pn": "A1761"}, {}, 0, True, 60, True, values)
                self.assertTrue(device["charging"])
                self.assertEqual(device["chargeTimeHours"], 1.5)
                values["output_power_total"] = 200
                device = normalize_device("sn", {"device_pn": "A1761"}, {}, 0, True, 60, True, values)
                self.assertFalse(device["charging"])
                self.assertTrue(device["discharging"])
                self.assertIsNone(device["chargeTimeHours"])

    def test_c1000_charge_evidence_must_be_fresh_and_complete(self):
        telemetry = Telemetry(None, None, 60)
        telemetry.record("sn", {"ac_input_power": 500, "dc_input_power": 0,
                                "output_power_total": 100}, 100)
        telemetry.record("sn", {"charging_status": 2, "remaining_time_hours": 1.5}, 150)
        raw = {"device_pn": "A1761", "input_power": 500, "output_power": 100}
        device = normalize_device("sn", raw, {}, 20, True, 60, True, telemetry.current("sn", 170))
        self.assertIsNone(device["charging"])
        self.assertIsNone(device["chargeTimeHours"])
        device = normalize_device("sn", raw, {}, 0, True, 60, True,
                                  {"charging_status": 2, "ac_input_power": 500,
                                   "output_power_total": 100})
        self.assertIsNone(device["charging"])

    def test_runtime_rejects_unsigned_maximum_time(self):
        device = self.device({"device_pn": "AS220", "mqtt_data": {
            "battery_status": 1, "remaining_time_hours": 6553.5,
        }}, 0)
        self.assertIsNone(device["runtimeHours"])

    def test_charge_time_expires_independently_of_charging_status(self):
        telemetry = Telemetry(None, None, 60)
        telemetry.record("sn", {"remaining_time_hours": 1.5}, 100)
        telemetry.record("sn", {"battery_status": 2}, 150)
        device = normalize_device("sn", {"device_pn": "AS220"}, {}, 20, True, 60, True,
                                  telemetry.current("sn", 170))
        self.assertTrue(device["charging"])
        self.assertIsNone(device["chargeTimeHours"])

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
            ("A1761", "charging_status", 3, None),
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
