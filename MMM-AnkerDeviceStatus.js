/* global Module */
Module.register("MMM-AnkerDeviceStatus", {
  defaults: {
    title: "Anker Power",
    showOffline: true,
    deviceIds: [],
    deviceNames: {},
    showBattery: true,
    showPower: true,
    showTemperature: false,
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
    this.sendSocketNotification("ANKER_UNSUBSCRIBE", { instanceId: this.identifier });
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

  getDom() {
    const wrapper = this.element("div", `anker-status${this.config.compact ? " anker-compact" : ""}`);
    if (this.config.title) wrapper.appendChild(this.element("div", "anker-title bright", this.config.title));
    if (!this.state) {
      wrapper.appendChild(this.element("div", "dimmed small", "Connecting to Anker..."));
      return wrapper;
    }
    if (this.state.error) wrapper.appendChild(this.element("div", "anker-error small", this.state.error));
    if (this.state.warning) wrapper.appendChild(this.element("div", "anker-warning small", this.state.warning));
    const devices = this.state.devices.filter((device) =>
      (this.config.showOffline || device.online !== false) &&
      (!this.config.deviceIds.length || this.config.deviceIds.includes(device.id))
    );
    if (!devices.length && !this.state.error) {
      wrapper.appendChild(this.element("div", "dimmed small", "No matching Anker devices found."));
    }
    for (const device of devices) {
      const card = this.element("div", "anker-card");
      const name = this.config.deviceNames[device.id] || device.name;
      card.appendChild(this.element("div", "anker-name bright", name));
      const connection = device.online === true ? "Online" : device.online === false ? "Offline" : "Status unknown";
      card.appendChild(this.element("div", `anker-connection small${device.online === false ? " dimmed" : ""}`,
        `${connection} | ${device.model || device.type}`));
      if (device.site) card.appendChild(this.element("div", "dimmed xsmall", device.site));
      if (device.status && device.status !== "unknown") {
        card.appendChild(this.element("div", "small", device.status));
      }
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
      if (this.config.showTemperature) this.metric(metrics, "Temperature", device.temperature, " C");
      card.appendChild(metrics);
      card.appendChild(this.element("div", "dimmed xsmall", device.telemetry));
      wrapper.appendChild(card);
    }
    if (this.config.showUpdated && this.state.fetchedAt) {
      const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(this.state.fetchedAt)) / 60000));
      wrapper.appendChild(this.element("div", "dimmed xsmall anker-updated",
        `${this.state.error ? "Last successful cloud poll" : "Cloud polled"} ${elapsed < 1 ? "just now" : `${elapsed} min ago`}`));
    }
    return wrapper;
  }
});
