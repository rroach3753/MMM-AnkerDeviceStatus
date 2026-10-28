const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");

const root = path.join(__dirname, "..");

class Element {
  constructor() {
    this.children = [];
    this.style = {};
    this.attributes = {};
    this.textContent = "";
  }
  appendChild(child) { this.children.push(child); }
  setAttribute(key, value) { this.attributes[key] = value; }
}

function text(element) {
  return [element.textContent, ...element.children.map(text)].join(" ");
}

function frontend() {
  let definition;
  const notifications = [];
  vm.runInNewContext(fs.readFileSync(path.join(root, "MMM-AnkerDeviceStatus.js"), "utf8"), {
    Module: { register: (_name, value) => { definition = value; } },
    document: { createElement: () => new Element() },
    setInterval: () => 1, clearInterval() {}, Date
  });
  return Object.assign(definition, {
    config: { ...definition.defaults }, identifier: "one",
    sendSocketNotification: (...args) => notifications.push(args), updateDom() {},
    notifications
  });
}

function backend() {
  let definition;
  let spawnCount = 0;
  let worker;
  const messages = [];
  const timers = [];
  vm.runInNewContext(fs.readFileSync(path.join(root, "node_helper.js"), "utf8"), {
    require: (name) => {
      if (name === "node_helper") return { create: (value) => { definition = value; return value; } };
      if (name === "node:child_process") return { spawn: () => {
        spawnCount++;
        worker = new EventEmitter();
        worker.stdout = new EventEmitter();
        worker.stdout.setEncoding = () => {};
        worker.kill = () => { worker.killed = true; };
        return worker;
      } };
      return require(name);
    },
    module: { exports: {} }, __dirname: root, process, Buffer,
    console: { error() {} },
    setTimeout: (callback) => { timers.push(callback); return timers.length; },
    clearTimeout() {}
  });
  definition.sendSocketNotification = (...args) => messages.push(args);
  definition.start();
  return { helper: definition, messages, timers, worker: () => worker, spawns: () => spawnCount };
}

const station = {
  id: "serial", name: "<img src=x onerror=alert(1)>", model: "A1761", type: "pps",
  online: true, status: "unknown", battery: 0, input: 0, output: null,
  solar: null, temperature: null, telemetry: "MQTT", site: ""
};

test("shows loading, zero battery and watts, and missing values", () => {
  const module = frontend();
  module.start();
  assert.match(text(module.getDom()), /Connecting/);
  module.socketNotificationReceived("ANKER_STATE", {
    instanceId: "one", devices: [station], fetchedAt: null, error: null
  });
  const rendered = text(module.getDom());
  assert.match(rendered, /Battery 0%/);
  assert.match(rendered, /Input 0 W/);
  assert.match(rendered, /Output --/);
  assert.match(rendered, /<img src=x/);
  assert.equal(module.getDom().children[1].children[0].children[0].children.length, 0);
});

test("places cloud poll age beside the title, not below cards", () => {
  const module = frontend();
  module.state = { devices: [station], fetchedAt: new Date(Date.now() - 120000).toISOString() };
  const dom = module.getDom();
  assert.equal(dom.children[0].className, "anker-title");
  assert.match(text(dom.children[0]), /Anker Power Cloud polled 2 min ago/);
  assert.equal(dom.children[0].children[1].className, "dimmed xsmall anker-updated");
  assert.equal(dom.children.at(-1).className, "anker-card");
  module.config.showUpdated = false;
  assert.doesNotMatch(text(module.getDom()), /Cloud polled/);
  module.config.showUpdated = true;
  module.config.title = "";
  assert.match(text(module.getDom().children[0]), /Cloud polled/);
  module.state.fetchedAt = null;
  assert.equal(module.getDom().children[0].className, "anker-card");
});

test("isolates instances and filters only explicitly offline devices", () => {
  const module = frontend();
  module.start();
  module.socketNotificationReceived("ANKER_STATE", { instanceId: "two", devices: [station] });
  assert.equal(module.state, null);
  module.config.showOffline = false;
  module.state = { devices: [station, { ...station, id: "offline", online: false }, { ...station, id: "unknown", online: null }] };
  assert.equal(module.getDom().children.filter((child) => child.className === "anker-card").length, 2);
  module.config.deviceIds = ["unknown"];
  module.config.deviceNames = { unknown: "Office" };
  assert.match(text(module.getDom()), /Office/);
  assert.equal(module.getDom().children.filter((child) => child.className === "anker-card").length, 1);
});

test("displays stale data with an error and handles suspension", () => {
  const module = frontend();
  module.start();
  module.state = { devices: [station], error: "Cloud failed", fetchedAt: new Date().toISOString() };
  assert.match(text(module.getDom()), /Cloud failed/);
  assert.match(text(module.getDom()), /Last successful cloud poll/);
  module.suspend();
  module.socketNotificationReceived("ANKER_STATE", { instanceId: "one", devices: [] });
  assert.equal(module.state.devices.length, 1);
  module.resume();
  assert.equal(module.notifications.at(-1)[0], "ANKER_SUBSCRIBE");
});

test("can hide cloud-only cards while retaining waiting and stale MQTT devices", () => {
  const module = frontend();
  module.state = { devices: [
    { ...station, telemetry: "Cloud only" },
    { ...station, telemetry: "Cloud only (MQTT disabled)" },
    { ...station, telemetry: "Waiting for MQTT" },
    { ...station, telemetry: "MQTT stale" },
    station
  ] };
  const cards = () => module.getDom().children.filter((child) => child.className === "anker-card");
  assert.equal(cards().length, 5);
  module.config.showCloudOnly = false;
  assert.equal(cards().length, 3);
  assert.doesNotMatch(text(module.getDom()), /Cloud only/);
});

test("shows temperature and explicit charging state including unavailable", () => {
  const module = frontend();
  module.config.showTemperature = true;
  module.state = { devices: [{ ...station, temperature: 25, charging: true }] };
  assert.match(text(module.getDom()), /Temperature 25 C \/ 77 F/);
  assert.match(text(module.getDom()), /Charging/);
  module.state.devices[0].charging = false;
  assert.match(text(module.getDom()), /Not Charging/);
  module.state.devices[0].charging = null;
  assert.match(text(module.getDom()), /Charging status unavailable/);
  module.state.devices[0].temperature = 0;
  assert.match(text(module.getDom()), /Temperature 0 C \/ 32 F/);
  module.state.devices[0].temperature = -10;
  assert.match(text(module.getDom()), /Temperature -10 C \/ 14 F/);
  module.state.devices[0].temperature = null;
  assert.match(text(module.getDom()), /Temperature --/);
});

test("groups name, connection and charging in the header without a model number", () => {
  const module = frontend();
  module.state = { devices: [{ ...station, name: "Office", charging: false }] };
  const card = module.getDom().children.find((child) => child.className === "anker-card");
  const header = card.children[0];
  assert.equal(header.className, "anker-device-header");
  assert.equal(header.children[0].textContent, "Office");
  assert.equal(header.children[1].textContent, "FW");
  assert.equal(header.children[2].textContent, "Online");
  assert.equal(header.children[3].textContent, "Not Charging");
  assert.doesNotMatch(text(card), /A1761/);
  module.config.showCharging = false;
  assert.doesNotMatch(text(module.getDom()), /Not Charging/);
});

test("shows firmware status beside the device name without guessing unknown state", () => {
  const module = frontend();
  module.state = { devices: [
    { ...station, id: "current", name: "Current", firmwareUpdate: false },
    { ...station, id: "update", name: "Update", firmwareUpdate: true },
    { ...station, id: "unknown", name: "Unknown", firmwareUpdate: null }
  ] };
  const cards = module.getDom().children.filter((child) => child.className === "anker-card");
  const firmware = cards.map((card) => card.children[0].children[1]);
  assert.deepEqual(firmware.map((label) => label.textContent), ["FW", "FW", "FW"]);
  assert.deepEqual(firmware.map((label) => label.className), [
    "anker-firmware anker-firmware-current",
    "anker-firmware anker-firmware-update",
    "anker-firmware anker-firmware-unknown"
  ]);
  assert.deepEqual(firmware.map((label) => label.attributes["aria-label"]), [
    "Firmware up to date",
    "Firmware update available",
    "Firmware status unavailable"
  ]);
  module.config.showFirmware = false;
  assert.doesNotMatch(text(module.getDom()), /FW/);
});

test("formats runtime only during discharge and handles missing data", () => {
  const module = frontend();
  module.state = { devices: [{ ...station, discharging: true, runtimeHours: 2.5 }] };
  assert.match(text(module.getDom()), /Runtime 2h 30m/);
  module.state.devices[0].runtimeHours = 1.999;
  assert.match(text(module.getDom()), /Runtime 2h 0m/);
  module.state.devices[0].runtimeHours = 0;
  assert.match(text(module.getDom()), /Runtime 0h 0m/);
  module.state.devices[0].runtimeHours = 25.5;
  assert.match(text(module.getDom()), /Runtime 25h 30m/);
  module.state.devices[0].runtimeHours = null;
  assert.match(text(module.getDom()), /Runtime --/);
  module.state.devices[0].discharging = false;
  assert.doesNotMatch(text(module.getDom()), /Runtime/);
  module.state.devices[0].discharging = null;
  assert.match(text(module.getDom()), /Runtime --/);
  module.config.showRuntime = false;
  assert.doesNotMatch(text(module.getDom()), /Runtime/);
});

test("shows time to full only when charging and formats valid estimates", () => {
  const module = frontend();
  for (const model of ["A1761", "AS220"]) {
    module.state = { devices: [{ ...station, model, charging: true, discharging: false }] };
    for (const [hours, expected] of [[1.5, "1h 30m"], [1.999, "2h 0m"], [0, "0h 0m"],
      [24, "24h 0m"], [24 + 1 / 60, "1d 0h 1m"], [25.5, "1d 1h 30m"],
      [48, "2d 0h 0m"], [47.999, "2d 0h 0m"], [50.25, "2d 2h 15m"],
      [null, "--"], [undefined, "--"], [-1, "--"], [Infinity, "--"], [NaN, "--"]]) {
      module.state.devices[0].chargeTimeHours = hours;
      assert.ok(text(module.getDom()).includes(`Time to full ${expected}`));
      assert.doesNotMatch(text(module.getDom()), /Runtime/);
    }
    for (const charging of [false, null, undefined]) {
      module.state.devices[0].charging = charging;
      assert.doesNotMatch(text(module.getDom()), /Time to full/);
    }
  }
  module.state.devices[0].charging = true;
  module.config.showChargeTime = false;
  assert.doesNotMatch(text(module.getDom()), /Time to full/);
  module.config.showChargeTime = true;
  module.state.devices[0].model = "A2345";
  module.config.solixOnly = false;
  assert.doesNotMatch(text(module.getDom()), /Time to full/);
});

test("includes supported chargers by default with a separate visibility toggle", () => {
  const module = frontend();
  module.state = { devices: [
    { ...station, name: "Rack Power", type: "pps" },
    { ...station, name: "Home", model: "A5101", type: "hes" },
    { ...station, name: "SOLIX fake name", model: "A2345", type: "charger" },
    { ...station, model: "A1340", type: "powerbank" },
    { ...station, model: "A1903", type: "device" },
    { ...station, model: "AS200", type: "charger" }
  ] };
  const cards = () => module.getDom().children.filter((child) => child.className === "anker-card");
  assert.equal(cards().length, 5);
  assert.match(text(module.getDom()), /SOLIX fake name/);
  module.config.showChargers = false;
  assert.equal(cards().length, 3);
  assert.doesNotMatch(text(module.getDom()), /SOLIX fake name/);
  module.config.showChargers = true;
  module.config.solixOnly = false;
  assert.equal(cards().length, 6);
  module.config.solixOnly = true;
  module.config.showChargers = false;
  module.config.showCloudOnly = false;
  module.state.devices[0].telemetry = "Cloud only";
  assert.equal(cards().length, 2);
});

const chargerDetails = [
  { key: "usbc_1_power", label: "USB-C 1 Power", group: "power", value: 0, unit: " W" },
  { key: "usbc_1_voltage", label: "USB-C 1 Voltage", group: "voltage", value: 20.125, unit: " V" },
  { key: "usbc_1_current", label: "USB-C 1 Current", group: "current", value: 2.345, unit: " A" },
  { key: "usage_mode", label: "Charging mode", group: "mode", value: "<b>Custom</b>", unit: "" }
];

test("charger groups and fields show by default and can each be hidden", () => {
  const module = frontend();
  module.state = { devices: [{ ...station, model: "A2345", type: "charger", chargerDetails }] };
  assert.match(text(module.getDom()), /USB-C 1 Power 0 W/);
  assert.match(text(module.getDom()), /USB-C 1 Voltage 20.125 V/);
  assert.match(text(module.getDom()), /USB-C 1 Current 2.345 A/);
  assert.match(text(module.getDom()), /<b>Custom<\/b>/);
  module.config.chargerGroups = { voltage: false };
  module.config.chargerFields = { usbc_1_power: false };
  assert.doesNotMatch(text(module.getDom()), /Port power|Port voltage|USB-C 1 Power|USB-C 1 Voltage/);
  assert.match(text(module.getDom()), /USB-C 1 Current/);
  module.config.showChargerDetails = false;
  assert.doesNotMatch(text(module.getDom()), /USB-C 1|Custom/);
  module.config.showChargerDetails = true;
  module.state.devices[0].chargerDetails = [];
  assert.match(text(module.getDom()), /Charger details unavailable/);
});

test("worker boundary accepts charger details and rejects malformed nested readings", () => {
  for (const invalid of [
    null, {}, [{ ...chargerDetails[0], value: {} }], [{ ...chargerDetails[0], group: "secrets" }],
    [{ ...chargerDetails[0], value: -1 }], [{ ...chargerDetails[0], key: "bad key" }],
    Array(257).fill(chargerDetails[0])
  ]) {
    const { helper, worker, messages } = backend();
    helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "one" });
    worker().stdout.emit("data", JSON.stringify({
      devices: [{ ...station, chargerDetails }], fetchedAt: null
    }) + "\n");
    assert.equal(messages.at(-1)[1].devices[0].chargerDetails.length, 4);
    worker().stdout.emit("data", JSON.stringify({
      devices: [{ ...station, chargerDetails: invalid }], fetchedAt: null
    }) + "\n");
    assert.match(messages.at(-1)[1].error, /invalid data/);
    assert.equal(messages.at(-1)[1].devices[0].chargerDetails.length, 4);
    helper.stop();
  }
});

test("shares one worker, parses fragmented responses, and targets subscribers", () => {
  const { helper, worker, spawns, messages } = backend();
  helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "one" });
  helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "two" });
  assert.equal(spawns(), 1);
  const state = JSON.stringify({ devices: [station], fetchedAt: null, error: null });
  worker().stdout.emit("data", state.slice(0, 10));
  assert.equal(messages.length, 0);
  worker().stdout.emit("data", state.slice(10) + "\n");
  assert.equal(messages.length, 2);
  assert.equal(messages[0][1].instanceId, "one");
  assert.equal(messages[1][1].instanceId, "two");
  helper.socketNotificationReceived("ANKER_UNSUBSCRIBE", { instanceId: "one" });
  assert.equal(worker().killed, undefined);
  helper.socketNotificationReceived("ANKER_UNSUBSCRIBE", { instanceId: "two" });
  assert.equal(worker().killed, undefined);
  assert.equal(helper.clients.size, 2);
  helper.stop();
  assert.equal(worker().killed, true);
  assert.equal(helper.clients.size, 0);
});

test("rejects malformed subscriptions and bounds retained routing IDs", () => {
  const { helper, spawns } = backend();
  for (const instanceId of [null, {}, "", "x".repeat(129)]) {
    helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId });
  }
  assert.equal(spawns(), 0);
  for (let index = 0; index < 40; index++) {
    helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: `instance-${index}` });
  }
  assert.equal(helper.clients.size, 32);
  assert.equal(spawns(), 1);
  helper.stop();
});

test("rejects bad helper output, preserves good data, and retries worker exits", () => {
  const { helper, worker, messages, timers, spawns } = backend();
  helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "one" });
  worker().stdout.emit("data", JSON.stringify({ devices: [station], fetchedAt: null, error: null }) + "\n");
  worker().stdout.emit("data", "bad JSON\n");
  assert.match(messages.at(-1)[1].error, /invalid data/);
  assert.equal(messages.at(-1)[1].devices.length, 1);
  assert.equal(worker().killed, true);
  worker().emit("close", 1);
  timers.at(-1)();
  assert.equal(spawns(), 2);
  helper.stop();
});

test("reports spawn errors without exposing credentials", () => {
  const { helper, worker, messages } = backend();
  helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "one" });
  worker().emit("error", new Error("private details"));
  assert.match(messages.at(-1)[1].error, /Cannot start/);
  assert.doesNotMatch(messages.at(-1)[1].error, /private details/);
  helper.stop();
});

test("rejects malformed device records and timestamps without losing good data", () => {
  for (const invalidState of [
    { devices: [null], fetchedAt: null },
    { devices: [{ ...station, telemetry: 42 }], fetchedAt: null },
    { devices: [{ ...station, battery: "50" }], fetchedAt: null },
    { devices: [{ ...station, chargeTimeHours: "1.5" }], fetchedAt: null },
    { devices: [station], fetchedAt: "not-a-date" },
    { devices: [station], fetchedAt: null, warning: {} }
  ]) {
    const { helper, worker, messages } = backend();
    helper.socketNotificationReceived("ANKER_SUBSCRIBE", { instanceId: "one" });
    worker().stdout.emit("data", JSON.stringify({ devices: [station], fetchedAt: null }) + "\n");
    worker().stdout.emit("data", JSON.stringify(invalidState) + "\n");
    assert.match(messages.at(-1)[1].error, /invalid data/);
    assert.equal(messages.at(-1)[1].devices[0].id, station.id);
    assert.equal(worker().killed, true);
    helper.stop();
  }
});
