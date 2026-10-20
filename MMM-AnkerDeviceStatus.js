const ANKER_SOLIX_TYPES = new Set([
  "solarbank", "solarbank_pps", "pps", "powerpanel", "hes",
  "home_backup", "combiner_box", "inverter", "smartmeter", "smartplug",
  "ev_charger", "powercooler", "generator"
]);
const ANKER_RUNTIME_MODELS = new Set(["A1761", "AS220"]);

Module.register("MMM-AnkerDeviceStatus", {
  defaults: {
    title: "Anker Power",
    showOffline: true,
    showCloudOnly: true,
    solixOnly: true,
    deviceIds: [],
    deviceNames: {},
    showBattery: true,
    showPower: true,
    showTemperature: false,
    showCharging: true,
    showRuntime: true,
    showChargeTime: true,
    showFirmware: true,
    showUpdated: true,
    compact: false
  },

  start() {
    this.state = null;
    this.suspended = false;
    this.sendSocketNotification("ANKER_SUBSCRIBE", { instanceId: this.identifier });
    this.ageTimer = setInterval(() => this.updateDom(0), 30000);
  },

  suspend() {
    this.suspended = true;
    clearInterval(this.ageTimer);
  },

  stop() {
    this.suspend();
  },

  resume() {
    this.suspended = false;
    this.sendSocketNotification("ANKER_SUBSCRIBE", { instanceId: this.identifier });
    clearInterval(this.ageTimer);
    this.ageTimer = setInterval(() => this.updateDom(0), 30000);
  },

  socketNotificationReceived(notification, payload) {
    if (this.suspended || notification !== "ANKER_STATE" || !payload ||
        payload.instanceId !== this.identifier) return;
    this.state = payload;
    this.updateDom(0);
  },

  getStyles() {
    return ["MMM-AnkerDeviceStatus.css"];
  },

  element(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  },

  metric(parent, label, value, unit) {
    const text = typeof value === "number" && Number.isFinite(value)
      ? `${Math.round(value)}${unit}` : "--";
    const row = this.element("div", "anker-metric");
    row.appendChild(this.element("span", "dimmed", label));
    row.appendChild(this.element("span", "bright", text));
    parent.appendChild(row);
  },

  durationMetric(parent, label, hours, showDays = false) {
    const minutes = typeof hours === "number" && Number.isFinite(hours) && hours >= 0
      ? Math.round(hours * 60) : null;
    const row = this.element("div", "anker-metric");
    const days = minutes !== null && showDays && minutes > 1440 ? Math.floor(minutes / 1440) : 0;
    row.appendChild(this.element("span", "dimmed", label));
    row.appendChild(this.element("span", "bright",
      minutes === null ? "--" :
        `${days ? `${days}d ` : ""}${Math.floor(minutes / 60) - days * 24}h ${minutes % 60}m`));
    parent.appendChild(row);
  },

  getDom() {
    const wrapper = this.element("div", `anker-status${this.config.compact ? " anker-compact" : ""}`);
    const title = this.element("div", "anker-title");
    if (this.config.title) title.appendChild(this.element("span", "bright", this.config.title));
    if (this.config.showUpdated && this.state && this.state.fetchedAt) {
      const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(this.state.fetchedAt)) / 60000));
      title.appendChild(this.element("span", "dimmed xsmall anker-updated",
        `${this.state.error ? "Last successful cloud poll" : "Cloud polled"} ${elapsed < 1 ? "just now" : `${elapsed} min ago`}`));
    }
    if (title.children.length) wrapper.appendChild(title);
    if (!this.state) {
      wrapper.appendChild(this.element("div", "dimmed small", "Connecting to Anker..."));
      return wrapper;
    }
    if (this.state.error) wrapper.appendChild(this.element("div", "anker-error small", this.state.error));
    if (this.state.warning) wrapper.appendChild(this.element("div", "anker-warning small", this.state.warning));
    const devices = this.state.devices.filter((device) =>
      (!this.config.solixOnly || ANKER_SOLIX_TYPES.has(device.type) || device.model === "AS200") &&
      (this.config.showOffline || device.online !== false) &&
      (this.config.showCloudOnly || !device.telemetry.startsWith("Cloud only")) &&
      (!this.config.deviceIds.length || this.config.deviceIds.includes(device.id))
    );
    if (!devices.length && !this.state.error) {
      wrapper.appendChild(this.element("div", "dimmed small", "No matching Anker devices found."));
    }
    for (const device of devices) {
      const card = this.element("div", "anker-card");
      const name = this.config.deviceNames[device.id] || device.name;
      const header = this.element("div", "anker-device-header");
      header.appendChild(this.element("span", "anker-name bright", name));
      if (this.config.showFirmware) {
        const firmware = device.firmwareUpdate === false ? "current" :
          device.firmwareUpdate === true ? "update" : "unknown";
        const label = firmware === "current" ? "Firmware up to date" :
          firmware === "update" ? "Firmware update available" : "Firmware status unavailable";
        const indicator = this.element("span", `anker-firmware anker-firmware-${firmware}`, "FW");
        indicator.setAttribute("aria-label", label);
        indicator.setAttribute("title", label);
        header.appendChild(indicator);
      }
      const connection = device.online === true ? "Online" : device.online === false ? "Offline" : "Status unknown";
      header.appendChild(this.element("span", `anker-connection xsmall${device.online === false ? " dimmed" : ""}`,
        connection));
      if (this.config.showCharging && ANKER_RUNTIME_MODELS.has(device.model)) {
        const charging = device.charging === true ? "Charging" :
          device.charging === false ? "Not Charging" : "Charging status unavailable";
        header.appendChild(this.element("span", "anker-charging xsmall", charging));
      } else if (device.status && device.status !== "unknown" && device.status.toLowerCase() !== connection.toLowerCase()) {
        header.appendChild(this.element("span", "anker-charging xsmall", device.status));
      }
      card.appendChild(header);
      if (device.site) card.appendChild(this.element("div", "dimmed xsmall", device.site));
      const metrics = this.element("div", "anker-metrics small");
      if (this.config.showBattery && (device.battery !== null || ["solarbank", "solarbank_pps", "pps", "hes"].includes(device.type))) {
        this.metric(metrics, "Battery", device.battery, "%");
        if (typeof device.battery === "number" && device.battery >= 0 && device.battery <= 100) {
          const track = this.element("div", "anker-battery-track");
          const fill = this.element("div", `anker-battery-fill${device.battery <= 20 ? " anker-low" : ""}`);
          fill.style.width = `${device.battery}%`;
          track.setAttribute("role", "progressbar");
          track.setAttribute("aria-valuenow", String(device.battery));
          track.setAttribute("aria-valuemin", "0");
          track.setAttribute("aria-valuemax", "100");
          track.setAttribute("aria-label", `${name} battery`);
          track.appendChild(fill);
          metrics.appendChild(track);
        }
      }
      if (this.config.showPower) {
        this.metric(metrics, "Input", device.input, " W");
        this.metric(metrics, "Output", device.output, " W");
        if (device.solar !== null) this.metric(metrics, "Solar", device.solar, " W");
      }
      if (this.config.showTemperature) {
        const row = this.element("div", "anker-metric");
        const temperature = device.temperature;
        const reading = typeof temperature === "number" && Number.isFinite(temperature)
          ? `${Math.round(temperature)} C / ${Math.round(temperature * 9 / 5 + 32)} F` : "--";
        row.appendChild(this.element("span", "dimmed", "Temperature"));
        row.appendChild(this.element("span", "bright", reading));
        metrics.appendChild(row);
      }
      if (this.config.showRuntime && ANKER_RUNTIME_MODELS.has(device.model) &&
          device.discharging !== false) {
        this.durationMetric(metrics, "Runtime", device.discharging === true ? device.runtimeHours : null);
      }
      if (this.config.showChargeTime && ANKER_RUNTIME_MODELS.has(device.model) &&
          device.charging === true) {
        this.durationMetric(metrics, "Time to full", device.chargeTimeHours, true);
      }
      card.appendChild(metrics);
      card.appendChild(this.element("div", "dimmed xsmall", device.telemetry));
      wrapper.appendChild(card);
    }
    return wrapper;
  }
});
