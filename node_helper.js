const NodeHelper = require("node_helper");
const { spawn } = require("node:child_process");
const path = require("node:path");

module.exports = NodeHelper.create({
  start() {
    this.clients = new Set();
    this.worker = null;
    this.latest = null;
    this.retryTimer = null;
    this.watchdog = null;
  },

  socketNotificationReceived(notification, payload) {
    if (!payload || typeof payload.instanceId !== "string") return;
    if (notification === "ANKER_SUBSCRIBE") {
      this.clients.add(payload.instanceId);
      if (this.latest) this.sendState(payload.instanceId);
      this.ensureWorker();
    } else if (notification === "ANKER_UNSUBSCRIBE") {
      this.clients.delete(payload.instanceId);
      if (!this.clients.size) this.stop();
    }
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
              !(state.fetchedAt === null || typeof state.fetchedAt === "string") ||
              !(state.error == null || typeof state.error === "string")) {
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
