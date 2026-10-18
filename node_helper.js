const NodeHelper = require("node_helper");
const { spawn } = require("node:child_process");
const path = require("node:path");

function isDevice(device) {
  return device && typeof device === "object" && !Array.isArray(device) &&
    ["id", "name", "model", "type", "status", "telemetry"].every((key) => typeof device[key] === "string") &&
    (device.site == null || typeof device.site === "string") &&
    ["battery", "input", "output", "solar", "temperature", "runtimeHours"].every((key) =>
      device[key] == null || (typeof device[key] === "number" && Number.isFinite(device[key]))) &&
    ["online", "charging", "discharging", "firmwareUpdate"].every((key) =>
      device[key] == null || typeof device[key] === "boolean");
}

module.exports = NodeHelper.create({
  start() {
    this.clients = new Set();
    this.worker = null;
    this.latest = null;
    this.retryTimer = null;
    this.watchdog = null;
  },

  socketNotificationReceived(notification, payload) {
    if (notification !== "ANKER_SUBSCRIBE") return;
    if (!payload || typeof payload.instanceId !== "string" ||
        !/^[A-Za-z0-9._:-]{1,128}$/.test(payload.instanceId)) {
      console.error("[MMM-AnkerDeviceStatus] Invalid subscription payload.");
      return;
    }
    if (!this.clients.has(payload.instanceId) && this.clients.size >= 32) {
      console.error("[MMM-AnkerDeviceStatus] Subscription limit reached.");
      return;
    }
    this.clients.add(payload.instanceId);
    if (this.latest) this.sendState(payload.instanceId);
    this.ensureWorker();
  },

  sendState(instanceId) {
    this.sendSocketNotification("ANKER_STATE", { ...this.latest, instanceId });
  },

  publish(state) {
    this.latest = state;
    for (const id of this.clients) this.sendState(id);
  },

  fail(message) {
    console.error(`[MMM-AnkerDeviceStatus] ${message}`);
    this.publish({
      devices: this.latest?.devices || [],
      fetchedAt: this.latest?.fetchedAt || null,
      error: message
    });
  },

  ensureWorker() {
    if (this.worker || this.retryTimer || !this.clients.size) return;
    const python = process.env.ANKER_PYTHON || path.join(
      __dirname, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
    );
    const worker = spawn(python, ["-u", path.join(__dirname, "anker_bridge.py")], {
      cwd: __dirname,
      stdio: ["ignore", "pipe", "ignore"],
      shell: false
    });
    this.worker = worker;
    let buffer = "";
    let reportedError = false;
    const armWatchdog = () => {
      clearTimeout(this.watchdog);
      this.watchdog = setTimeout(() => {
        reportedError = true;
        this.fail("Anker helper timed out. Check the network and Python setup.");
        worker.kill();
      }, 180000);
    };
    armWatchdog();
    worker.stdout.setEncoding("utf8");
    worker.stdout.on("data", (chunk) => {
      if (this.worker !== worker) return;
      buffer += chunk;
      if (Buffer.byteLength(buffer) > 1048576) {
        reportedError = true;
        this.fail("Anker helper response exceeded the size limit.");
        worker.kill();
        return;
      }
      let newline;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        try {
          const state = JSON.parse(line);
          if (!state || !Array.isArray(state.devices) ||
              !state.devices.every(isDevice) ||
              !(state.fetchedAt === null || (typeof state.fetchedAt === "string" && Number.isFinite(Date.parse(state.fetchedAt)))) ||
              !(state.error == null || typeof state.error === "string") ||
              !(state.warning == null || typeof state.warning === "string")) {
            throw new Error("Invalid state");
          }
          reportedError = Boolean(state.error);
          if (state.error && state.error !== this.latest?.error) {
            console.error(`[MMM-AnkerDeviceStatus] ${state.error}`);
          }
          this.publish(state);
          armWatchdog();
        } catch {
          reportedError = true;
          this.fail("Anker helper returned invalid data. Check the installed client version.");
          worker.kill();
          return;
        }
      }
    });
    worker.on("error", () => {
      if (this.worker !== worker) return;
      reportedError = true;
      this.fail("Cannot start Anker Python helper. Create the module's .venv or set ANKER_PYTHON in MagicMirror's environment.");
    });
    worker.on("close", () => {
      if (this.worker !== worker) return;
      this.worker = null;
      clearTimeout(this.watchdog);
      if (!reportedError) this.fail("Anker helper stopped unexpectedly; retrying in 60 seconds.");
      if (this.clients.size) {
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null;
          this.ensureWorker();
        }, 60000);
      }
    });
  },

  stop() {
    this.clients.clear();
    clearTimeout(this.retryTimer);
    clearTimeout(this.watchdog);
    this.retryTimer = null;
    const worker = this.worker;
    this.worker = null;
    if (worker) worker.kill();
    if (this.latest) {
      this.latest = { ...this.latest, error: "Monitoring paused; waiting for fresh Anker data." };
    }
  }
});
