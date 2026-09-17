const MODE_LABELS = { baseline: "Baseline", snake: "Snake" };

class SessionInterface {
  constructor() {
    this.state = null;
    this.busy = false;
    this.reconnectDelay = 500;
    this.toastTimer = null;
    this.browser = null;
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
      const folder = event.target.closest("[data-folder-path]");
      if (folder) return this.browse(folder.dataset.folderPath);
      const resume = event.target.closest("[data-resume]");
      if (resume) {
        const acknowledged = document.getElementById("acknowledge-mismatch").checked;
        return this.post("/api/session/resume", {
          pseudonym: resume.dataset.resume,
          acknowledge_mismatch: acknowledged,
        });
      }
      const blockStart = event.target.closest("[data-block-start]");
      if (blockStart) {
        return this.post(`/api/experiment/blocks/${blockStart.dataset.blockStart}/start`);
      }
      const blockEnd = event.target.closest("[data-block-end]");
      if (blockEnd) {
        const label = blockEnd.dataset.phase || "block";
        if (!window.confirm(`End this ${label} block? This action advances the experiment sequence.`)) return;
        return this.post(`/api/experiment/blocks/${blockEnd.dataset.blockEnd}/end`, { confirmed: true });
      }
      const blockAbort = event.target.closest("[data-block-abort]");
      if (blockAbort) {
        return this.post(`/api/experiment/blocks/${blockAbort.dataset.blockAbort}/abort`);
      }
      const control = event.target.closest("[data-block-control]");
      if (control) {
        return this.post(`/api/experiment/blocks/${control.dataset.blockControl}/control`, {
          active: control.dataset.active === "true",
        });
      }
      const restart = event.target.closest("[data-restart-stack]");
      if (restart) {
        if (!window.confirm("Restart the experiment stack and restore the current control state?")) return;
        return this.post(`/api/experiment/blocks/${restart.dataset.restartStack}/restart-stack`);
      }
    });
    document.getElementById("validate-button").addEventListener("click", () =>
      this.post("/api/checkup/validate")
    );
    document.getElementById("reset-button").addEventListener("click", () =>
      this.post("/api/checkup/reset")
    );
    document.getElementById("browse-button").addEventListener("click", () => this.browse(""));
    document.getElementById("close-browser-button").addEventListener("click", () => this.closeBrowser());
    document.getElementById("browser-up-button").addEventListener("click", () => {
      if (this.browser?.parent) this.browse(this.browser.parent);
    });
    document.getElementById("select-folder-button").addEventListener("click", async () => {
      if (!this.browser) return;
      await this.post("/api/session/root", { path: this.browser.path });
      this.closeBrowser();
      this.resetParticipantForm();
      await this.regeneratePseudonym();
    });
    document.getElementById("folder-create-form").addEventListener("submit", (event) => this.createFolder(event));
    document.getElementById("change-parent-button").addEventListener("click", async () => {
      await this.post("/api/session/reset");
      await this.browse("");
    });
    document.getElementById("regenerate-button").addEventListener("click", () => this.regeneratePseudonym());
    document.getElementById("participant-form").addEventListener("submit", (event) => this.createParticipant(event));
    document.getElementById("cancel-participant-button").addEventListener("click", () => this.post("/api/session/cancel"));
    document.getElementById("launch-experiment-button").addEventListener("click", () => this.post("/api/session/launch"));
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
      if (url.startsWith("/api/session/")) {
        const stateResponse = await fetch("/api/state");
        const state = await stateResponse.json();
        this.update(state);
        if (["/api/session/cancel", "/api/session/reset"].includes(url)) {
          this.resetParticipantForm();
          if (state.enrollment?.selected_parent) await this.regeneratePseudonym();
        }
      } else {
        this.update(payload);
      }
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

  async browse(path) {
    try {
      const response = await fetch(`/api/session/browse?path=${encodeURIComponent(path)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Unable to browse folders.");
      this.renderBrowser(payload);
    } catch (error) {
      this.showToast(error.message, true);
    }
  }

  renderBrowser(payload) {
    this.browser = payload;
    document.getElementById("folder-dialog").hidden = false;
    document.getElementById("browser-path").textContent = payload.path;
    document.getElementById("browser-up-button").disabled = !payload.parent;
    document.getElementById("browser-directories").innerHTML = payload.directories.length
        ? payload.directories.map((item) => `<button class="folder-entry" data-folder-path="${this.escape(item.path)}" type="button"><span>📁</span>${this.escape(item.name)}</button>`).join("")
        : '<p class="supporting-copy">No subfolders.</p>';
  }

  async createFolder(event) {
    event.preventDefault();
    if (!this.browser) return;
    const input = document.getElementById("new-folder-name");
    try {
      const response = await fetch("/api/session/folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_path: this.browser.path, name: input.value }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Unable to create folder.");
      input.value = "";
      this.renderBrowser(payload);
      this.showToast("Folder created. You can select it or create a subfolder.");
    } catch (error) {
      this.showToast(error.message, true);
    }
  }

  closeBrowser() {
    document.getElementById("folder-dialog").hidden = true;
    document.getElementById("new-folder-name").value = "";
  }

  async regeneratePseudonym() {
    try {
      const response = await fetch("/api/session/pseudonym/regenerate", { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Unable to generate pseudonym.");
      document.getElementById("pseudonym").value = payload.pseudonym;
    } catch (error) {
      this.showToast(error.message, true);
    }
  }

  createParticipant(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    return this.post("/api/session/new", {
      pseudonym: values.get("pseudonym"),
      gathered_consent: values.get("gathered_consent") === "true",
      handedness: values.get("handedness"),
      joystick_experience: values.get("joystick_experience") === "true",
      visual_or_motor_impairment: values.get("visual_or_motor_impairment") === "true",
    });
  }

  resetParticipantForm() {
    const form = document.getElementById("participant-form");
    form.reset();
    document.getElementById("pseudonym").value = "";
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
    const currentPanel = this.state.current_panel;
    const onPanelC = currentPanel === "C";
    const onPanelD = currentPanel === "D";
    const onBlockPanel = ["E", "F", "G"].includes(currentPanel);
    document.getElementById("panel-b").hidden = currentPanel !== "B";
    document.getElementById("panel-c").hidden = !onPanelC;
    document.getElementById("panel-d").hidden = !onPanelD;
    for (const panel of ["E", "F", "G"]) {
      document.getElementById(`panel-${panel.toLowerCase()}`).hidden = currentPanel !== panel;
    }
    document.querySelectorAll("[data-panel-step]").forEach((step) => {
      const current = step.dataset.panelStep === currentPanel;
      step.classList.toggle("is-current", current);
      const panelOrder = ["B", "C", "D", "E", "F", "G"];
      const completed = panelOrder.indexOf(step.dataset.panelStep) < panelOrder.indexOf(currentPanel);
      step.classList.toggle("is-locked", !current && !completed);
      step.classList.toggle("is-complete", completed);
    });
    if (onPanelC) return this.renderEnrollment();
    if (onPanelD) return this.renderExperiment();
    if (onBlockPanel) return this.renderActiveBlock();

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

  renderEnrollment() {
    const enrollment = this.state.enrollment;
    if (!enrollment) return;
    const labels = {
      awaiting_parent: "Awaiting folder", parent_selected: "Folder selected",
      participant_ready: "Participant ready", launching: "Launching", error: "Error",
    };
    const pill = document.getElementById("enrollment-status");
    pill.textContent = labels[enrollment.workflow] || enrollment.workflow;
    pill.className = `pill ${enrollment.error ? "pill--error" : enrollment.participant ? "pill--active" : "pill--neutral"}`;
    document.getElementById("selected-parent").textContent = enrollment.selected_parent || `Root: ${enrollment.sessions_root}`;
    document.getElementById("change-parent-button").hidden = !enrollment.selected_parent;
    document.getElementById("participant-selection").hidden = !enrollment.selected_parent || !!enrollment.participant;
    document.getElementById("participant-ready").hidden = !enrollment.participant;

    const warnings = document.getElementById("session-warnings");
    warnings.hidden = !enrollment.warnings.length;
    warnings.innerHTML = enrollment.warnings.length
      ? `<strong>Folder warnings</strong><ul>${enrollment.warnings.map((item) => `<li>${this.escape(item)}</li>`).join("")}</ul>` : "";

    const resumeList = document.getElementById("resume-list");
    resumeList.innerHTML = enrollment.resume_candidates.length
      ? enrollment.resume_candidates.map((item) => `<article class="resume-entry"><div><strong>${this.escape(item.pseudonym)}</strong><span>${this.escape(item.session_date)} · ${this.escape(item.experimental_plan)}</span></div><button class="secondary-button" data-resume="${this.escape(item.pseudonym)}" type="button">Resume</button></article>`).join("")
      : '<p class="supporting-copy">No resumable session.</p>';

    const mismatch = document.getElementById("mismatch-warning");
    mismatch.hidden = !enrollment.mismatches.length;
    document.getElementById("mismatch-list").innerHTML = enrollment.mismatches.map((item) => `<li>${this.escape(item.path)}</li>`).join("");

    if (enrollment.participant) {
      const participant = enrollment.participant;
      document.getElementById("participant-heading").textContent = `${participant.pseudonym} is ready`;
      document.getElementById("participant-summary").innerHTML = [
        ["Session", participant.resumed ? "Resumed" : "New"],
        ["Plan", participant.experimental_plan], ["Handedness", participant.handedness],
        ["Started", participant.starting_time], ["Folder", participant.folder],
      ].map(([term, value]) => `<div><dt>${this.escape(term)}</dt><dd>${this.escape(value)}</dd></div>`).join("");
    }
    document.getElementById("launch-experiment-button").disabled = this.busy || !enrollment.can_launch;
    document.getElementById("cancel-participant-button").disabled = this.busy;
    const error = document.getElementById("enrollment-error");
    error.hidden = !enrollment.error;
    error.textContent = enrollment.error || "";
  }

  renderExperiment() {
    const experiment = this.state.experiment;
    if (!experiment?.progress) return;
    const progress = experiment.progress;
    const statusLabels = {
      ready: "Ready", interrupted: "Interrupted", error: "Action required",
      sequence_completed: "Sequence complete", starting_block: "Starting",
    };
    const status = document.getElementById("experiment-status");
    status.textContent = statusLabels[progress.workflow] || progress.workflow.replaceAll("_", " ");
    status.className = `pill ${progress.workflow === "sequence_completed" ? "pill--active" : ["interrupted", "error"].includes(progress.workflow) ? "pill--warning" : "pill--neutral"}`;
    document.getElementById("experiment-plan").textContent = experiment.participant.experimental_plan.replace("->", " → ");
    const next = progress.blocks.find((block) => block.status !== "completed");
    document.getElementById("block-list").innerHTML = progress.blocks.map((block) => {
      const isNext = next?.id === block.id;
      const labels = { not_started: "Locked", starting: "Starting", running: "Running", completed: "Complete", interrupted: "Interrupted", error: "Error" };
      const pillClass = block.status === "completed" ? "pill--active" : ["interrupted", "error"].includes(block.status) ? "pill--warning" : "pill--neutral";
      const startable = isNext && ["not_started", "interrupted", "error"].includes(block.status) && progress.workflow !== "sequence_completed";
      const verb = block.status === "interrupted" ? "Resume" : block.status === "error" ? "Retry" : "Start";
      return `<article class="block-entry${isNext ? " is-next" : ""}">
        <span class="block-order">${block.order}</span>
        <div class="block-copy"><strong>${this.escape(this.title(block.mode))} · ${this.escape(this.title(block.phase))}</strong><span>${this.escape(block.mode_role.replace("_", " "))} · folder ${this.escape(block.folder)}${block.attempts ? ` · ${block.attempts} attempt${block.attempts === 1 ? "" : "s"}` : ""}</span></div>
        <span class="pill ${pillClass}">${labels[block.status] || this.escape(block.status)}</span>
        <button class="secondary-button" data-block-start="${this.escape(block.id)}" type="button" ${this.busy || !startable ? "disabled" : ""}>${verb} ${this.escape(block.phase)}</button>
      </article>`;
    }).join("");
    const warnings = document.getElementById("experiment-warning");
    warnings.hidden = !progress.warnings.length;
    warnings.innerHTML = progress.warnings.length ? `<strong>Session warning</strong><ul>${progress.warnings.map((warning) => `<li>${this.escape(warning)}</li>`).join("")}</ul>` : "";
    document.getElementById("process-summary").innerHTML = [
      ["Stack", experiment.stack.status], ["Mapper", experiment.mapper.status],
      ["Active mode", experiment.mapper.active_mode || "None"], ["Profile", experiment.profile.sha256.slice(0, 12)],
    ].map(([term, value]) => `<div><dt>${this.escape(term)}</dt><dd>${this.escape(this.title(value))}</dd></div>`).join("");
    const error = document.getElementById("experiment-error");
    error.hidden = !experiment.error;
    error.textContent = experiment.error || "";
  }

  renderActiveBlock() {
    const experiment = this.state.experiment;
    const progress = experiment?.progress;
    if (!progress) return;
    const block = progress.blocks.find((item) => item.id === progress.current_block);
    if (!block) return;
    const panel = document.getElementById(`panel-${block.panel.toLowerCase()}`);
    if (block.phase === "discovery") return this.renderDiscovery(panel, block, experiment);
    const phase = this.title(block.phase);
    const settings = Object.entries(block.settings)
      .map(([key, value]) => `<div><dt>${this.escape(key.replaceAll("_", " "))}</dt><dd>${this.escape(JSON.stringify(value))}</dd></div>`).join("");
    panel.innerHTML = `<p class="step-number">Panel ${block.panel} · ${phase}</p>
      <h2>${this.escape(this.title(block.mode))} ${this.escape(block.phase)}</h2>
      <p class="supporting-copy">The experiment stack and the ${this.escape(this.title(block.mode))} mapper are active. Detailed ${this.escape(block.phase)} logic will be implemented in its dedicated lot.</p>
      <dl class="participant-summary block-metadata"><div><dt>Block</dt><dd>${this.escape(block.id)}</dd></div><div><dt>Folder</dt><dd>${this.escape(block.folder)}</dd></div><div><dt>Attempt</dt><dd>${block.attempts}</dd></div>${settings}</dl>
      <div class="block-actions"><button class="text-button" data-restart-stack="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Restart stack</button><button class="reject-button" data-block-abort="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Stop and return</button><button class="primary-button" data-block-end="${this.escape(block.id)}" data-phase="${this.escape(block.phase)}" type="button" ${this.busy ? "disabled" : ""}>End ${this.escape(block.phase)}</button></div>`;
  }

  renderDiscovery(panel, block, experiment) {
    const instructions = block.settings.instructions || {};
    const segments = experiment.segments || [];
    const recorder = experiment.recorder || { status: "inactive", storage: "mcap" };
    const segmentRows = segments.length ? segments.map((segment) => {
      const counts = segment.message_counts || {};
      const countText = block.settings.rosbag_topics.map((topic) => `${topic}: ${counts[topic] || 0}`).join(" · ");
      const pillClass = segment.valid ? "pill--active" : segment.status === "recording" ? "pill--warning" : "pill--error";
      const label = segment.status === "recording" ? "Recording" : segment.valid ? "Valid" : this.title(segment.status);
      return `<article class="segment-entry"><div><strong>${this.escape(segment.name)}</strong><span>${this.escape(countText)}</span></div><span class="pill ${pillClass}">${this.escape(label)}</span></article>`;
    }).join("") : '<p class="supporting-copy">No recording segment yet.</p>';
    const controlActive = experiment.control_active;
    const processItems = [
      ["Stack", experiment.stack.status],
      ["Mapper", experiment.mapper.status],
      ["Recorder", recorder.status],
      ["Storage", recorder.storage || "mcap"],
    ].map(([term, value]) => `<div><dt>${this.escape(term)}</dt><dd>${this.escape(this.title(value))}</dd></div>`).join("");
    panel.className = "panel-page";
    panel.innerHTML = `<section class="hero-card"><div><p class="step-number">Panel E · Discovery</p><h2>${this.escape(this.title(block.mode))} discovery</h2><p class="supporting-copy">Free movement without specified or validated targets.</p></div><span class="pill ${controlActive ? "pill--active" : "pill--neutral"}">${controlActive ? "Control and recording active" : "Control inactive"}</span></section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Instructions</p><h2>Standardised instructions</h2></div>${instructions.placeholder ? '<span class="pill pill--warning">Placeholder</span>' : ""}</div><p class="instruction-copy">${this.escape(instructions.text || "")}</p>${instructions.placeholder ? '<p class="inline-warning">Replace this placeholder before running participant sessions.</p>' : ""}</section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Control</p><h2>Mode and recording</h2></div><span class="supporting-copy">${this.escape(block.folder)}</span></div><dl class="participant-summary process-summary">${processItems}</dl><div class="discovery-controls"><button class="${controlActive ? "reject-button" : "primary-button"}" data-block-control="${this.escape(block.id)}" data-active="${controlActive ? "false" : "true"}" type="button" ${this.busy ? "disabled" : ""}>${controlActive ? "Deactivate mode and recording" : "Activate mode and recording"}</button><button class="text-button" data-restart-stack="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Restart stack</button></div>${block.error ? `<p class="inline-error">${this.escape(block.error)}</p>` : ""}</section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Recordings</p><h2>MCAP segments</h2></div><span class="pill ${experiment.can_end ? "pill--active" : "pill--warning"}">${experiment.can_end ? "Required data verified" : "Valid segment required"}</span></div><div class="segment-list">${segmentRows}</div></section>
      <section class="validation-card"><div><p class="step-number">Complete discovery</p><h2>Return to the experiment sequence</h2><p class="supporting-copy">At least one segment must contain messages for all three expected topics.</p></div><div class="validation-actions"><button class="reject-button" data-block-abort="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Stop and return</button><button class="primary-button" data-block-end="${this.escape(block.id)}" data-phase="discovery" type="button" ${this.busy || !experiment.can_end ? "disabled" : ""}>End discovery</button></div></section>`;
  }

  title(value) {
    const text = String(value || "").replaceAll("_", " ");
    return text ? text[0].toUpperCase() + text.slice(1) : text;
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
