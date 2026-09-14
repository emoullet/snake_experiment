const POSES = [
  ["target_1", "Target 1", "Record target 1"],
  ["target_out_1", "Target out 1", "Record target out 1"],
  ["target_2", "Target 2", "Record target 2"],
  ["target_out_2", "Target out 2", "Record target out 2"],
  ["target_3", "Target 3", "Record target 3"],
  ["target_out_3", "Target out 3", "Record target out 3"],
  ["starting_point", "Starting point", "Record starting point"],
];

class PanelA {
  constructor() {
    this.state = null;
    this.websocket = null;
    this.reconnectDelay = 500;
    this.toastTimer = null;
    this.busy = false;
    this.renderCaptureShells();
    this.bindActions();
    this.connect();
  }

  renderCaptureShells() {
    for (const [poseId, title, action] of POSES) {
      const card = document.querySelector(`[data-pose-id="${poseId}"]`);
      card.innerHTML = `
        <div>
          <div class="capture-card__top">
            <h3>${title}</h3>
            <span class="pill pill--neutral">Not recorded</span>
          </div>
          <div class="pose-slot"></div>
        </div>
        <button class="record-button" type="button" data-record="${poseId}" disabled>
          ${action}
        </button>`;
    }
  }

  bindActions() {
    document.addEventListener("click", async (event) => {
      const stackButton = event.target.closest("[data-stack-action]");
      if (stackButton) {
        const action = stackButton.dataset.stackAction;
        const message = action === "start" ? "Experiment stack started." : "Experiment stack stopped.";
        await this.post(`/api/stack/${action}`, message);
        return;
      }
      const modeButton = event.target.closest("[data-mode]");
      if (modeButton) {
        await this.post(`/api/modes/${modeButton.dataset.mode}`, "Control mode activated.");
        return;
      }
      const recordButton = event.target.closest("[data-record]");
      if (recordButton) {
        await this.post(`/api/poses/${recordButton.dataset.record}`, "Pose recorded.");
      }
    });
    document.getElementById("save-button").addEventListener("click", () => {
      this.post("/api/calibrations", "Calibration saved.", true);
    });
  }

  async post(url, successMessage, includeFileName = false) {
    this.setActionDisabled(true);
    try {
      const response = await fetch(url, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "The operation failed.");
      }
      if (payload.state) {
        this.update(payload.state);
      } else {
        this.update(payload);
      }
      const suffix = includeFileName && payload.file_name ? ` ${payload.file_name}` : "";
      this.showToast(successMessage + suffix);
    } catch (error) {
      this.showToast(error.message, true);
    } finally {
      this.setActionDisabled(false);
      if (this.state) this.render();
    }
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.websocket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    this.websocket.onopen = () => {
      this.reconnectDelay = 500;
      this.renderConnection(true);
    };
    this.websocket.onmessage = (event) => this.update(JSON.parse(event.data));
    this.websocket.onclose = () => {
      this.renderConnection(false);
      window.setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 8000);
    };
    this.websocket.onerror = () => this.websocket.close();
  }

  update(state) {
    this.state = state;
    this.render();
  }

  renderConnection(connected) {
    const dot = document.getElementById("connection-dot");
    dot.className = `status-dot ${connected ? "status-dot--ok" : "status-dot--error"}`;
    document.getElementById("connection-label").textContent = connected ? "Connected" : "Disconnected";
  }

  render() {
    const { stack, mode, pose_stream: stream, captures } = this.state;
    const stackStatus = document.getElementById("stack-status");
    const stackStatusLabel = stack.status[0].toUpperCase() + stack.status.slice(1);
    stackStatus.textContent = stackStatusLabel;
    stackStatus.className = `pill ${
      stack.status === "active"
        ? "pill--active"
        : stack.status === "error"
          ? "pill--error"
          : stack.status === "inactive"
            ? "pill--neutral"
            : "pill--busy"
    }`;
    const stackButton = document.getElementById("stack-button");
    const stackIsActive = stack.status === "active";
    stackButton.dataset.stackAction = stackIsActive ? "stop" : "start";
    stackButton.textContent = stackIsActive
      ? "Stop experiment stack"
      : stack.status === "starting"
        ? "Starting experiment stack…"
        : stack.status === "stopping"
          ? "Stopping experiment stack…"
          : "Start experiment stack";
    stackButton.classList.toggle("is-stop", stackIsActive);
    stackButton.disabled = this.busy || ["starting", "stopping"].includes(stack.status);
    document.getElementById("stack-help").textContent = stack.use_simulation
      ? "Simulation stack · joystick mapper controlled separately below."
      : "Hardware stack · joystick mapper controlled separately below.";
    const stackError = document.getElementById("stack-error");
    stackError.hidden = !stack.error;
    stackError.textContent = stack.error || "";

    const modeStatus = document.getElementById("mode-status");
    const modeLabel = mode.active_mode
      ? `${mode.active_mode[0].toUpperCase()}${mode.active_mode.slice(1)} active`
      : mode.status[0].toUpperCase() + mode.status.slice(1);
    modeStatus.textContent = modeLabel;
    modeStatus.className = `pill ${
      mode.status === "active"
        ? "pill--active"
        : mode.status === "error"
          ? "pill--error"
          : mode.status === "inactive"
            ? "pill--neutral"
            : "pill--busy"
    }`;

    document.querySelectorAll("[data-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.mode === mode.active_mode);
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode.active_mode));
      button.disabled = this.busy;
    });
    const modeError = document.getElementById("mode-error");
    modeError.hidden = !mode.error;
    modeError.textContent = mode.error || "";

    const streamElement = document.getElementById("pose-stream");
    const dotClass = stream.fresh
      ? "status-dot--ok"
      : stream.received
        ? "status-dot--warning"
        : "status-dot--waiting";
    const streamLabel = stream.fresh
      ? `${this.state.pose_topic} · ${stream.frame_id}`
      : stream.received
        ? `${this.state.pose_topic} is stale`
        : `Waiting for ${this.state.pose_topic}`;
    streamElement.innerHTML = `<span class="status-dot ${dotClass}"></span><span>${this.escapeHtml(streamLabel)}</span>`;

    for (const [poseId] of POSES) {
      const card = document.querySelector(`[data-pose-id="${poseId}"]`);
      const capture = captures[poseId];
      const pill = card.querySelector("[class^='pill']");
      pill.textContent = capture ? "Recorded" : "Not recorded";
      pill.className = `pill ${capture ? "pill--recorded" : "pill--neutral"}`;
      card.querySelector(".pose-slot").innerHTML = capture
        ? this.poseDetails(capture)
        : "";
      card.querySelector("[data-record]").disabled = this.busy || !this.state.can_capture;
    }

    const saveButton = document.getElementById("save-button");
    saveButton.disabled = this.busy || !this.state.can_save;
    document.getElementById("save-help").textContent = this.state.can_save
      ? "All seven poses are ready to save."
      : this.state.save_blocker || "Calibration is not ready to save.";
    document.getElementById("save-directory").textContent = this.state.calibration_directory;
  }

  poseDetails(pose) {
    const p = pose.position;
    const q = pose.orientation;
    return `
      <details class="pose-details">
        <summary>View pose details</summary>
        <dl class="pose-grid">
          <div><dt>Frame</dt><dd>${this.escapeHtml(pose.frame_id)}</dd></div>
          <div><dt>Mode</dt><dd>${this.escapeHtml(pose.control_mode)}</dd></div>
          <div><dt>Position</dt><dd>${this.vector(p)}</dd></div>
          <div><dt>Quaternion</dt><dd>${this.vector(q)}</dd></div>
          <div><dt>ROS stamp</dt><dd>${pose.stamp_sec}.${String(pose.stamp_nanosec).padStart(9, "0")}</dd></div>
          <div><dt>Captured</dt><dd>${this.escapeHtml(pose.captured_at_utc)}</dd></div>
        </dl>
      </details>`;
  }

  vector(values) {
    return Object.entries(values)
      .map(([key, value]) => `${key} ${Number(value).toFixed(5)}`)
      .join(" · ");
  }

  escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  setActionDisabled(disabled) {
    this.busy = disabled;
    if (this.state) this.render();
  }

  showToast(message, isError = false) {
    const toast = document.getElementById("toast");
    window.clearTimeout(this.toastTimer);
    toast.textContent = message;
    toast.className = `toast${isError ? " toast--error" : ""}`;
    toast.hidden = false;
    this.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }
}

document.addEventListener("DOMContentLoaded", () => new PanelA());
