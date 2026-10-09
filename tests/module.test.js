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
  assert.equal(module.getDom().children[1].children[0].children.length, 0);
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
  assert.equal(worker().killed, true);
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
