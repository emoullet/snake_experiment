const MODE_LABELS = { baseline: "Baseline", snake: "Snake" };

class SessionInterface {
  constructor() {
    this.state = null;
    this.busy = false;
    this.reconnectDelay = 500;
    this.toastTimer = null;
    this.bindActions();
    this.connect();
  }

  bindActions() {
    document.addEventListener("click", async (event) => {
      const diagnosticSummary = event.target.closest(".diagnostic-list summary");
      if (diagnosticSummary) {
        event.preventDefault();
        return;
      }
      const stack = event.target.closest("[data-stack]");
      if (stack) return this.post(`/api/stack/${stack.dataset.stack}`);
      const start = event.target.closest("[data-mode-start]");
      if (start) {
        const mode = start.dataset.modeStart;
        const status = this.state?.modes?.[mode]?.status;
        const action = status === "failed" ? "retry" : "start";
        return this.post(`/api/checkup/modes/${mode}/${action}`);
      }
      const confirm = event.target.closest("[data-confirm]");
      if (confirm) {
        return this.post(`/api/checkup/modes/${confirm.dataset.confirm}/confirm`, {
          accepted: confirm.dataset.accepted === "true",
        });
      }
    });
    document.getElementById("validate-button").addEventListener("click", () =>
      this.post("/api/checkup/validate")
    );
    document.getElementById("reset-button").addEventListener("click", () =>
      this.post("/api/checkup/reset")
    );
  }

  async post(url, body = null) {
    this.busy = true;
    this.render();
    try {
      const options = { method: "POST", headers: {} };
      if (body !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "The operation failed.");
      this.update(payload);
    } catch (error) {
      this.showToast(error.message, true);
    } finally {
      this.busy = false;
      this.render();
    }
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    socket.onopen = () => {
      this.reconnectDelay = 500;
      this.renderConnection(true);
    };
    socket.onmessage = (event) => this.update(JSON.parse(event.data));
    socket.onclose = () => {
      this.renderConnection(false);
      window.setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 8000);
    };
    socket.onerror = () => socket.close();
  }

  update(state) {
    this.state = state;
    this.render();
  }

  renderConnection(connected) {
    document.getElementById("connection-dot").className = `status-dot ${connected ? "status-dot--ok" : "status-dot--error"}`;
    document.getElementById("connection-label").textContent = connected ? "Connected" : "Disconnected";
  }

  render() {
    if (!this.state) return;
    const onPanelC = this.state.current_panel === "C";
    document.getElementById("panel-b").hidden = onPanelC;
    document.getElementById("panel-c").hidden = !onPanelC;
    document.querySelectorAll("[data-panel-step]").forEach((step) => {
      const current = step.dataset.panelStep === this.state.current_panel;
      step.classList.toggle("is-current", current);
      step.classList.toggle("is-locked", !current && step.dataset.panelStep !== "B");
      step.classList.toggle("is-complete", onPanelC && step.dataset.panelStep === "B");
    });
    if (onPanelC) return;

    const workflow = this.state.workflow.replaceAll("_", " ");
    const workflowPill = document.getElementById("workflow-status");
    workflowPill.textContent = workflow[0].toUpperCase() + workflow.slice(1);
    workflowPill.className = `pill ${this.state.error ? "pill--error" : this.state.can_validate ? "pill--active" : "pill--neutral"}`;

    const stack = this.state.stack;
    const stackPill = document.getElementById("stack-status");
    stackPill.textContent = stack.status[0].toUpperCase() + stack.status.slice(1);
    stackPill.className = `pill ${stack.status === "active" ? "pill--active" : stack.status === "error" ? "pill--error" : "pill--neutral"}`;
    const stackButton = document.getElementById("stack-button");
    const active = stack.status === "active";
    stackButton.dataset.stack = active ? "stop" : "start";
    stackButton.textContent = active ? "Stop experiment stack" : "Start experiment stack";
    stackButton.classList.toggle("is-danger", active);
    stackButton.disabled = this.busy || ["starting", "stopping"].includes(stack.status);
    document.getElementById("stack-help").textContent = `${stack.use_simulation ? "Simulation" : "Hardware"} profile · launch ownership remains with this interface.`;

    for (const mode of ["baseline", "snake"]) this.renderMode(mode);
    document.getElementById("validate-button").disabled = this.busy || !this.state.can_validate;
    document.getElementById("validation-help").textContent = this.state.can_validate
      ? "Both modes passed. Validation will stop the stack and continue to Panel C."
      : "Both modes must pass before validation.";
    document.getElementById("reset-button").disabled = this.busy;
    const error = document.getElementById("global-error");
    error.hidden = !this.state.error;
    error.textContent = this.state.error || "";
  }

  renderMode(mode) {
    const result = this.state.modes[mode];
    const card = document.querySelector(`[data-mode-card="${mode}"]`);
    const pill = card.querySelector(".mode-status");
    const labels = {
      not_tested: "Not tested", checking: "Checking", awaiting_joystick: "Move joystick",
      awaiting_confirmation: "Confirm behaviour", passed: "Passed", failed: "Failed",
    };
    pill.textContent = labels[result.status] || result.status;
    pill.className = `pill mode-status ${result.status === "passed" ? "pill--active" : result.status === "failed" ? "pill--error" : ["checking", "awaiting_joystick", "awaiting_confirmation"].includes(result.status) ? "pill--warning" : "pill--neutral"}`;
    const guidance = card.querySelector(".mode-guidance");
    guidance.textContent = this.guidance(result);
    const start = card.querySelector(".mode-start");
    start.textContent = result.status === "failed" ? `Retry ${MODE_LABELS[mode].toLowerCase()} mode` : `Check ${MODE_LABELS[mode].toLowerCase()} mode`;
    const anotherModeRunning = this.state.active_mode && this.state.active_mode !== mode;
    start.disabled = this.busy || this.state.stack.status !== "active" || anotherModeRunning || ["checking", "awaiting_joystick", "awaiting_confirmation", "passed"].includes(result.status);
    card.querySelector(".confirmation").hidden = result.status !== "awaiting_confirmation";
    const failures = result.evidence?.checks?.filter((check) => !check.passed) || [];
    const diagnosticList = card.querySelector(".diagnostic-list");
    diagnosticList.innerHTML = failures.length
      ? `<details open><summary>${failures.length} pending diagnostic${failures.length > 1 ? "s" : ""}</summary><ul>${failures.map((item) => `<li>${this.escape(item.name)} · expected ${this.escape(JSON.stringify(item.expected))}, observed ${this.escape(JSON.stringify(item.observed))}</li>`).join("")}</ul></details>`
      : result.evidence ? `<p class="diagnostics-ok">Automatic diagnostics passed.</p>` : "";
  }

  guidance(result) {
    if (result.error) return result.error;
    if (result.status === "checking") return "Diagnostics are converging. Move the joystick through its controls.";
    if (result.status === "awaiting_joystick") return "Move an axis beyond the deadzone or press a joystick button.";
    if (result.status === "awaiting_confirmation") return "Automatic checks passed. Confirm the physical behaviour.";
    if (result.status === "passed") return `Validated in ${result.attempts} attempt${result.attempts === 1 ? "" : "s"}.`;
    return "Start the stack before checking this mode.";
  }

  escape(value) {
    return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  showToast(message, error = false) {
    const toast = document.getElementById("toast");
    window.clearTimeout(this.toastTimer);
    toast.textContent = message;
    toast.className = `toast${error ? " toast--error" : ""}`;
    toast.hidden = false;
    this.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4500);
  }
}

document.addEventListener("DOMContentLoaded", () => new SessionInterface());
