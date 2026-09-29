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
      const checkupGoTo = event.target.closest("[data-checkup-go-to]");
      if (checkupGoTo) {
        if (!window.confirm(`Move the robot to ${checkupGoTo.dataset.checkupGoTo.replaceAll("_", " ")}?`)) return;
        return this.post("/api/checkup/go-to", { pose_id: checkupGoTo.dataset.checkupGoTo });
      }
      if (event.target.closest("[data-checkup-go-to-stop]")) {
        return this.post("/api/checkup/go-to/stop");
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
      const presentation = event.target.closest("[data-presentation]");
      if (presentation) {
        return this.post(`/api/experiment/presentation/${presentation.dataset.presentation}`);
      }
      const explanation = event.target.closest("[data-mode-explanation]");
      if (explanation) {
        return this.post(`/api/experiment/blocks/${explanation.dataset.blockId}/explanation/${explanation.dataset.modeExplanation}`);
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
      const blockGoTo = event.target.closest("[data-block-go-to]");
      if (blockGoTo) {
        const poseId = blockGoTo.dataset.blockGoTo;
        if (!window.confirm(`Suspend manual control and move the robot to ${poseId.replaceAll("_", " ")}?`)) return;
        return this.post(`/api/experiment/blocks/${blockGoTo.dataset.blockId}/go-to`, { pose_id: poseId });
      }
      const blockGoToStop = event.target.closest("[data-block-go-to-stop]");
      if (blockGoToStop) {
        return this.post(`/api/experiment/blocks/${blockGoToStop.dataset.blockId}/go-to/stop`);
      }
      const incidentOccurrence = event.target.closest("[data-incident-occurrence]");
      if (incidentOccurrence) {
        return this.post(
          `/api/experiment/blocks/${incidentOccurrence.dataset.blockId}/${incidentOccurrence.dataset.trialPhase}/incidents`
        );
      }
      const trialAction = event.target.closest("[data-trial-action]");
      if (trialAction) {
        if (
          trialAction.dataset.trialAction === "prepare"
          && !window.confirm("Prepare the next trial and move the robot to its calibrated start pose?")
        ) return;
        return this.post(`/api/experiment/blocks/${trialAction.dataset.blockId}/${trialAction.dataset.trialPhase}/${trialAction.dataset.trialAction}`);
      }
      const trialResolution = event.target.closest("[data-trial-resolve]");
      if (trialResolution) {
        return this.post(`/api/experiment/blocks/${trialResolution.dataset.blockId}/${trialResolution.dataset.trialPhase}/resolve`, {
          decision: trialResolution.dataset.trialResolve,
        });
      }
    });
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-trial-incident-review-form]");
      if (!form) return;
      event.preventDefault();
      const blockId = form.dataset.blockId;
      const phase = form.dataset.trialPhase;
      const descriptions = [...form.querySelectorAll("[data-incident-input]")].map(
        (input) => ({ id: Number(input.dataset.incidentId), text: input.value })
      );
      const invalidatesAttempt = event.submitter?.dataset.incidentInvalidates === "true";
      this.post(`/api/experiment/blocks/${blockId}/${phase}/incidents/review`, {
        descriptions,
        invalidates_attempt: invalidatesAttempt,
      });
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
    this.setHidden(document.getElementById("folder-dialog"), false);
    this.setText(document.getElementById("browser-path"), payload.path);
    this.setDisabled(document.getElementById("browser-up-button"), !payload.parent);
    this.patchMarkup(
      document.getElementById("browser-directories"),
      payload.directories.length
        ? payload.directories.map((item) => `<button class="folder-entry" data-dom-key="folder:${this.escape(item.path)}" data-folder-path="${this.escape(item.path)}" type="button"><span>📁</span>${this.escape(item.name)}</button>`).join("")
        : '<p class="supporting-copy">No subfolders.</p>',
    );
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
    this.setHidden(document.getElementById("folder-dialog"), true);
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

  setText(element, value) {
    const next = String(value);
    if (element.textContent !== next) element.textContent = next;
  }

  setClass(element, value) {
    if (element.className !== value) element.className = value;
  }

  setHidden(element, value) {
    const next = Boolean(value);
    if (element.hidden !== next) element.hidden = next;
  }

  setDisabled(element, value) {
    const next = Boolean(value);
    if (element.disabled !== next) element.disabled = next;
  }

  setAttribute(element, name, value) {
    if (element.getAttribute(name) !== value) element.setAttribute(name, value);
  }

  domKey(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    if (node.id) return `id:${node.id}`;
    const key = node.getAttribute("data-dom-key");
    return key === null ? null : `item:${key}`;
  }

  sameNodeKind(current, desired) {
    return current.nodeType === desired.nodeType
      && (current.nodeType !== Node.ELEMENT_NODE || current.tagName === desired.tagName);
  }

  patchElement(current, desired) {
    if (current.nodeType === Node.TEXT_NODE) {
      if (current.nodeValue !== desired.nodeValue) current.nodeValue = desired.nodeValue;
      return;
    }
    if (current.nodeType !== Node.ELEMENT_NODE) return;
    for (const attribute of [...current.attributes]) {
      if (current.tagName === "DETAILS" && attribute.name === "open") continue;
      if (!desired.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of [...desired.attributes]) {
      if (current.tagName === "DETAILS" && attribute.name === "open") continue;
      this.setAttribute(current, attribute.name, attribute.value);
    }
    // The operator owns a review field's value until the form is submitted.
    if (current.tagName === "TEXTAREA") return;
    this.patchChildren(current, desired);
  }

  patchChildren(parent, desiredParent) {
    let cursor = parent.firstChild;
    for (const desired of [...desiredParent.childNodes]) {
      const key = this.domKey(desired);
      const matching = key === null
        ? (cursor && this.domKey(cursor) === null && this.sameNodeKind(cursor, desired) ? cursor : null)
        : [...parent.childNodes].find((node) =>
            this.domKey(node) === key && this.sameNodeKind(node, desired));
      const current = matching || desired.cloneNode(true);
      if (current !== cursor) parent.insertBefore(current, cursor);
      if (matching) this.patchElement(current, desired);
      cursor = current.nextSibling;
    }
    while (cursor) {
      const next = cursor.nextSibling;
      parent.removeChild(cursor);
      cursor = next;
    }
  }

  patchMarkup(container, markup, scope = null) {
    if (scope !== null && container.dataset.renderScope !== scope) {
      container.replaceChildren();
      container.dataset.renderScope = scope;
    }
    const range = document.createRange();
    range.selectNodeContents(container);
    this.patchChildren(container, range.createContextualFragment(markup));
  }

  renderConnection(connected) {
    this.setClass(document.getElementById("connection-dot"), `status-dot ${connected ? "status-dot--ok" : "status-dot--error"}`);
    this.setText(document.getElementById("connection-label"), connected ? "Connected" : "Disconnected");
  }

  render() {
    if (!this.state) return;
    const currentPanel = this.state.current_panel;
    const onPanelC = currentPanel === "C";
    const onPanelD = currentPanel === "D";
    const onBlockPanel = ["E", "F", "G"].includes(currentPanel);
    this.setHidden(document.getElementById("panel-b"), currentPanel !== "B");
    this.setHidden(document.getElementById("panel-c"), !onPanelC);
    this.setHidden(document.getElementById("panel-d"), !onPanelD);
    for (const panel of ["E", "F", "G"]) {
      this.setHidden(document.getElementById(`panel-${panel.toLowerCase()}`), currentPanel !== panel);
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
    this.setText(workflowPill, workflow[0].toUpperCase() + workflow.slice(1));
    this.setClass(workflowPill, `pill ${this.state.error ? "pill--error" : this.state.can_validate ? "pill--active" : "pill--neutral"}`);

    const stack = this.state.stack;
    const stackPill = document.getElementById("stack-status");
    this.setText(stackPill, stack.status[0].toUpperCase() + stack.status.slice(1));
    this.setClass(stackPill, `pill ${stack.status === "active" ? "pill--active" : stack.status === "error" ? "pill--error" : "pill--neutral"}`);
    const stackButton = document.getElementById("stack-button");
    const active = stack.status === "active";
    this.setAttribute(stackButton, "data-stack", active ? "stop" : "start");
    this.setText(stackButton, active ? "Stop experiment stack" : "Start experiment stack");
    stackButton.classList.toggle("is-danger", active);
    this.setDisabled(stackButton, this.busy || ["starting", "stopping"].includes(stack.status));
    this.setText(document.getElementById("stack-help"), `${stack.use_simulation ? "Simulation" : "Hardware"} profile · launch ownership remains with this interface.`);

    for (const mode of ["baseline", "snake"]) this.renderMode(mode);
    const goTo = this.state.go_to || {};
    const goToMotion = goTo.motion?.current;
    const goToActive = !!goTo.motion?.active;
    const goToResults = goTo.results || {};
    const completedGoTo = Object.values(goToResults).filter(Boolean).length;
    const goToPill = document.getElementById("goto-status");
    this.setText(goToPill, goToActive
      ? `${this.title(goToMotion?.status)} · ${this.title(goToMotion?.pose_id)}`
      : `${completedGoTo}/4 poses reached`);
    this.setClass(goToPill, `pill ${completedGoTo === 4 ? "pill--active" : goToActive ? "pill--warning" : "pill--neutral"}`);
    this.setText(document.getElementById("goto-help"), goToMotion?.error
      ? goToMotion.error
      : goToActive
        ? `Linear error: ${goToMotion.linear_error_mm === null ? "—" : Number(goToMotion.linear_error_mm).toFixed(1) + " mm"} · Angular error: ${goToMotion.angular_error_deg === null ? "—" : Number(goToMotion.angular_error_deg).toFixed(1) + "°"}`
        : "The four calibrated poses must be reached before validation.");
    document.querySelectorAll("[data-checkup-go-to]").forEach((button) => {
      const succeeded = !!goToResults[button.dataset.checkupGoTo];
      const latestAttempt = [...(goTo.history || [])]
        .reverse()
        .find((attempt) => attempt.pose_id === button.dataset.checkupGoTo);
      const timedOut = !succeeded && latestAttempt?.status === "timed_out";
      const label = button.dataset.checkupGoTo === "starting_point"
        ? "Go to starting point"
        : `Go to target ${button.dataset.checkupGoTo.replace("target_", "")}`;
      const reachedLabel = button.dataset.checkupGoTo === "starting_point"
        ? "starting point reached"
        : `target ${button.dataset.checkupGoTo.replace("target_", "")} reached`;
      this.setDisabled(button, this.busy || !goTo.available || goToActive);
      button.classList.toggle("is-complete", succeeded);
      button.classList.toggle("is-timed-out", timedOut);
      this.setText(button, succeeded
        ? reachedLabel
        : timedOut
          ? `! ${label} · timeout`
          : label);
      this.setAttribute(
        button,
        "aria-label",
        succeeded
          ? `${label}, successfully reached`
          : timedOut
            ? `${label}, not reached before timeout`
            : label,
      );
    });
    const stopMotion = document.getElementById("checkup-stop-motion");
    this.setHidden(stopMotion, !goToActive);
    this.setDisabled(stopMotion, this.busy || !goToActive);
    this.setDisabled(document.getElementById("validate-button"), this.busy || !this.state.can_validate);
    this.setText(document.getElementById("validation-help"), this.state.can_validate
      ? "Both modes passed. Validation will stop the stack and continue to Panel C."
      : "Both modes and all four Go-to checks must pass before validation.");
    this.setDisabled(document.getElementById("reset-button"), this.busy);
    const error = document.getElementById("global-error");
    this.setHidden(error, !this.state.error);
    this.setText(error, this.state.error || "");
  }

  renderEnrollment() {
    const enrollment = this.state.enrollment;
    if (!enrollment) return;
    const labels = {
      awaiting_parent: "Awaiting folder", parent_selected: "Folder selected",
      participant_ready: "Participant ready", launching: "Launching", error: "Error",
    };
    const pill = document.getElementById("enrollment-status");
    this.setText(pill, labels[enrollment.workflow] || enrollment.workflow);
    this.setClass(pill, `pill ${enrollment.error ? "pill--error" : enrollment.participant ? "pill--active" : "pill--neutral"}`);
    this.setText(document.getElementById("selected-parent"), enrollment.selected_parent || `Root: ${enrollment.sessions_root}`);
    this.setHidden(document.getElementById("change-parent-button"), !enrollment.selected_parent);
    this.setHidden(document.getElementById("participant-selection"), !enrollment.selected_parent || !!enrollment.participant);
    this.setHidden(document.getElementById("participant-ready"), !enrollment.participant);

    const warnings = document.getElementById("session-warnings");
    this.setHidden(warnings, !enrollment.warnings.length);
    this.patchMarkup(warnings, enrollment.warnings.length
      ? `<strong>Folder warnings</strong><ul>${enrollment.warnings.map((item, index) => `<li data-dom-key="warning:${index}">${this.escape(item)}</li>`).join("")}</ul>` : "");

    const resumeList = document.getElementById("resume-list");
    this.patchMarkup(resumeList, enrollment.resume_candidates.length
      ? enrollment.resume_candidates.map((item) => `<article class="resume-entry" data-dom-key="participant:${this.escape(item.pseudonym)}"><div><strong>${this.escape(item.pseudonym)}</strong><span>${this.escape(item.session_date)} · ${this.escape(item.experimental_plan)}</span></div><button class="secondary-button" data-resume="${this.escape(item.pseudonym)}" type="button">Resume</button></article>`).join("")
      : '<p class="supporting-copy">No resumable session.</p>');

    const mismatch = document.getElementById("mismatch-warning");
    this.setHidden(mismatch, !enrollment.mismatches.length);
    this.patchMarkup(document.getElementById("mismatch-list"), enrollment.mismatches.map((item) => `<li data-dom-key="mismatch:${this.escape(item.path)}">${this.escape(item.path)}</li>`).join(""));

    if (enrollment.participant) {
      const participant = enrollment.participant;
      this.setText(document.getElementById("participant-heading"), `${participant.pseudonym} is ready`);
      this.patchMarkup(document.getElementById("participant-summary"), [
        ["Session", participant.resumed ? "Resumed" : "New"],
        ["Plan", participant.experimental_plan], ["Handedness", participant.handedness],
        ["Started", participant.starting_time], ["Folder", participant.folder],
      ].map(([term, value]) => `<div data-dom-key="summary:${term}"><dt>${this.escape(term)}</dt><dd>${this.escape(value)}</dd></div>`).join(""));
    }
    this.setDisabled(document.getElementById("launch-experiment-button"), this.busy || !enrollment.can_launch);
    this.setDisabled(document.getElementById("cancel-participant-button"), this.busy);
    const error = document.getElementById("enrollment-error");
    this.setHidden(error, !enrollment.error);
    this.setText(error, enrollment.error || "");
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
    this.setText(status, statusLabels[progress.workflow] || progress.workflow.replaceAll("_", " "));
    this.setClass(status, `pill ${progress.workflow === "sequence_completed" ? "pill--active" : ["interrupted", "error"].includes(progress.workflow) ? "pill--warning" : "pill--neutral"}`);
    this.setText(document.getElementById("experiment-plan"), experiment.participant.experimental_plan.replace("->", " → "));
    const presentation = progress.presentation || { status: "legacy_skipped" };
    const presentationStatus = document.getElementById("presentation-status");
    const presentationLabels = { pending: "Pending", showing: "On participant screen", completed: "Done", legacy_skipped: "Legacy session" };
    this.setText(presentationStatus, presentationLabels[presentation.status] || presentation.status);
    this.setClass(presentationStatus, `pill ${presentation.status === "completed" ? "pill--active" : presentation.status === "showing" ? "pill--warning" : "pill--neutral"}`);
    this.setHidden(document.getElementById("presentation-video-warning"), !!experiment.presentation_video_available || presentation.status === "legacy_skipped");
    this.setDisabled(document.getElementById("presentation-show-button"), this.busy || !["pending", "showing", "completed"].includes(presentation.status) || !!progress.current_block);
    this.setDisabled(document.getElementById("presentation-complete-button"), this.busy || presentation.status !== "showing");
    const next = progress.blocks.find((block) => block.status !== "completed");
    this.patchMarkup(document.getElementById("block-list"), progress.blocks.map((block) => {
      const isNext = next?.id === block.id;
      const labels = { not_started: "Locked", starting: "Starting", running: "Running", completed: "Complete", interrupted: "Interrupted", error: "Error" };
      const pillClass = block.status === "completed" ? "pill--active" : ["interrupted", "error"].includes(block.status) ? "pill--warning" : "pill--neutral";
      const startable = isNext && ["not_started", "interrupted", "error"].includes(block.status) && progress.workflow !== "sequence_completed" && ["completed", "legacy_skipped"].includes(presentation.status);
      const verb = block.status === "interrupted" ? "Resume" : block.status === "error" ? "Retry" : "Start";
      return `<article class="block-entry${isNext ? " is-next" : ""}" data-dom-key="block:${this.escape(block.id)}">
        <span class="block-order">${block.order}</span>
        <div class="block-copy"><strong>${this.escape(this.title(block.mode))} · ${this.escape(this.title(block.phase))}</strong><span>${this.escape(block.mode_role.replace("_", " "))} · folder ${this.escape(block.folder)}${block.attempts ? ` · ${block.attempts} attempt${block.attempts === 1 ? "" : "s"}` : ""}</span></div>
        <span class="pill ${pillClass}">${labels[block.status] || this.escape(block.status)}</span>
        <button class="secondary-button" data-block-start="${this.escape(block.id)}" type="button" ${this.busy || !startable ? "disabled" : ""}>${verb} ${this.escape(block.phase)}</button>
      </article>`;
    }).join(""));
    const warnings = document.getElementById("experiment-warning");
    this.setHidden(warnings, !progress.warnings.length);
    this.patchMarkup(warnings, progress.warnings.length ? `<strong>Session warning</strong><ul>${progress.warnings.map((warning, index) => `<li data-dom-key="warning:${index}">${this.escape(warning)}</li>`).join("")}</ul>` : "");
    this.patchMarkup(document.getElementById("process-summary"), [
      ["Stack", experiment.stack.status], ["Mapper", experiment.mapper.status],
      ["Active mode", experiment.mapper.active_mode || "None"], ["Profile", experiment.profile.sha256.slice(0, 12)],
    ].map(([term, value]) => `<div data-dom-key="summary:${term}"><dt>${this.escape(term)}</dt><dd>${this.escape(this.title(value))}</dd></div>`).join(""));
    const error = document.getElementById("experiment-error");
    this.setHidden(error, !experiment.error);
    this.setText(error, experiment.error || "");
  }

  renderActiveBlock() {
    const experiment = this.state.experiment;
    const progress = experiment?.progress;
    if (!progress) return;
    const block = progress.blocks.find((item) => item.id === progress.current_block);
    if (!block) return;
    const panel = document.getElementById(`panel-${block.panel.toLowerCase()}`);
    if (block.phase === "discovery") return this.renderDiscovery(panel, block, experiment);
    if (block.phase === "training") return this.renderTraining(panel, block, experiment);
    if (block.phase === "recording") return this.renderRecording(panel, block, experiment);
    const phase = this.title(block.phase);
    const settings = Object.entries(block.settings)
      .map(([key, value]) => `<div><dt>${this.escape(key.replaceAll("_", " "))}</dt><dd>${this.escape(JSON.stringify(value))}</dd></div>`).join("");
    this.patchMarkup(panel, `<p class="step-number">Panel ${block.panel} · ${phase}</p>
      <h2>${this.escape(this.title(block.mode))} ${this.escape(block.phase)}</h2>
      <p class="supporting-copy">The experiment stack and the ${this.escape(this.title(block.mode))} mapper are active. Detailed ${this.escape(block.phase)} logic will be implemented in its dedicated lot.</p>
      <dl class="participant-summary block-metadata"><div><dt>Block</dt><dd>${this.escape(block.id)}</dd></div><div><dt>Folder</dt><dd>${this.escape(block.folder)}</dd></div><div><dt>Attempt</dt><dd>${block.attempts}</dd></div>${settings}</dl>
      <div class="block-actions"><button class="text-button" data-restart-stack="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Restart stack</button><button class="reject-button" data-block-abort="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Stop and return</button><button class="primary-button" data-block-end="${this.escape(block.id)}" data-phase="${this.escape(block.phase)}" type="button" ${this.busy ? "disabled" : ""}>End ${this.escape(block.phase)}</button></div>`, `${experiment.participant.folder}:${block.id}`);
  }

  renderDiscovery(panel, block, experiment) {
    const instructions = block.settings.instructions || {};
    const explanation = block.mode_explanation || { status: "pending" };
    const explanationDone = explanation.status === "completed";
    const segments = experiment.segments || [];
    const recorder = experiment.recorder || { status: "inactive", storage: "mcap" };
    const segmentRows = segments.length ? segments.map((segment) => {
      const counts = segment.message_counts || {};
      const countText = block.settings.rosbag_topics.map((topic) => `${topic}: ${counts[topic] || 0}`).join(" · ");
      const pillClass = segment.valid ? "pill--active" : segment.status === "recording" ? "pill--warning" : "pill--error";
      const label = segment.status === "recording" ? "Recording" : segment.valid ? "Valid" : this.title(segment.status);
      return `<article class="segment-entry" data-dom-key="segment:${this.escape(segment.name)}"><div><strong>${this.escape(segment.name)}</strong><span>${this.escape(countText)}</span></div><span class="pill ${pillClass}">${this.escape(label)}</span></article>`;
    }).join("") : '<p class="supporting-copy">No recording segment yet.</p>';
    const controlActive = experiment.control_active;
    const processItems = [
      ["Stack", experiment.stack.status],
      ["Mapper", experiment.mapper.status],
      ["Recorder", recorder.status],
      ["Storage", recorder.storage || "mcap"],
    ].map(([term, value]) => `<div data-dom-key="summary:${term}"><dt>${this.escape(term)}</dt><dd>${this.escape(this.title(value))}</dd></div>`).join("");
    this.setClass(panel, "panel-page");
    this.patchMarkup(panel, `<section class="hero-card"><div><p class="step-number">Panel E · Discovery</p><h2>${this.escape(this.title(block.mode))} discovery</h2><p class="supporting-copy">Free movement without specified or validated targets.</p></div><span class="pill ${controlActive ? "pill--active" : "pill--neutral"}">${controlActive ? "Control and recording active" : "Control inactive"}</span></section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Participant panel B</p><h2>Mode explanation</h2></div><span class="pill ${explanationDone ? "pill--active" : explanation.status === "showing" ? "pill--warning" : "pill--neutral"}">${explanationDone ? "Done" : explanation.status === "showing" ? "On participant screen" : "Pending"}</span></div><p class="supporting-copy">Show the ${this.escape(this.title(block.mode))} video on the participant screen, then confirm the explanation.</p>${!experiment.mode_explanation_video_available ? '<p class="inline-warning">Mode explanation video is unavailable. Manual validation remains possible for development.</p>' : ""}<div class="discovery-controls"><button class="text-button" data-mode-explanation="show" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || explanationDone ? "disabled" : ""}>Show mode explanation</button><button class="primary-button" data-mode-explanation="complete" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || explanation.status !== "showing" ? "disabled" : ""}>Explanation done</button></div></section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Instructions</p><h2>Standardised instructions</h2></div>${instructions.placeholder ? '<span class="pill pill--warning">Placeholder</span>' : ""}</div><p class="instruction-copy">${this.escape(instructions.text || "")}</p>${instructions.placeholder ? '<p class="inline-warning">Replace this placeholder before running participant sessions.</p>' : ""}</section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Control</p><h2>Mode and recording</h2></div><span class="supporting-copy">${this.escape(block.folder)}</span></div><dl class="participant-summary process-summary">${processItems}</dl><div class="discovery-controls"><button class="${controlActive ? "reject-button" : "primary-button"}" data-block-control="${this.escape(block.id)}" data-active="${controlActive ? "false" : "true"}" type="button" ${this.busy || (!controlActive && !explanationDone) ? "disabled" : ""}>${controlActive ? "Deactivate mode and recording" : "Activate mode and recording"}</button><button class="text-button" data-restart-stack="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Restart stack</button></div>${block.error ? `<p class="inline-error">${this.escape(block.error)}</p>` : ""}</section>
      <section class="card"><div class="section-heading"><div><p class="step-number">Recordings</p><h2>MCAP segments</h2></div><span class="pill ${experiment.can_end ? "pill--active" : "pill--warning"}">${experiment.can_end ? "Required data verified" : "Valid segment required"}</span></div><div class="segment-list">${segmentRows}</div></section>
      <section class="validation-card"><div><p class="step-number">Complete discovery</p><h2>Return to the experiment sequence</h2><p class="supporting-copy">Confirm the explanation and collect at least one segment with messages for all three expected topics.</p></div><div class="validation-actions"><button class="reject-button" data-block-abort="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Stop and return</button><button class="primary-button" data-block-end="${this.escape(block.id)}" data-phase="discovery" type="button" ${this.busy || !experiment.can_end ? "disabled" : ""}>End discovery</button></div></section>`, `${experiment.participant.folder}:${block.id}`);
  }

  renderTraining(panel, block, experiment) {
    return this.renderTrialPanel(panel, block, experiment, "training", "F", "Training");
  }

  renderRecording(panel, block, experiment) {
    return this.renderTrialPanel(panel, block, experiment, "recording", "G", "Recording");
  }

  renderTrialPanel(panel, block, experiment, phase, panelLetter, title) {
    const training = experiment[phase] || {};
    const trials = training.trials || [];
    const workflow = training.workflow || "awaiting_prepare";
    const current = training.current_trial || trials.find((trial) => trial.id === training.current_trial_id) || null;
    const live = training.live || {};
    const recording = workflow === "recording";
    const completed = trials.filter((trial) => ["completed", "completed_with_deviation"].includes(trial.status)).length;
    const currentAttempt = current?.attempts?.length ? current.attempts[current.attempts.length - 1] : null;
    const errorValue = (error, unit) => error ? `${Number(error[unit]).toFixed(2)} ${unit.endsWith("mm") ? "mm" : "deg"}` : "—";
    const trialRows = trials.map((trial) => {
      const statusClass = trial.status === "completed" ? "pill--active" : trial.status === "completed_with_deviation" ? "pill--warning" : trial.status === "decision_required" ? "pill--error" : "pill--neutral";
      return `<article class="segment-entry" data-dom-key="trial:${trial.id}"><div><strong>Trial ${trial.id} · cycle ${trial.cycle}</strong><span>Target ${trial.target_start} → ${trial.target_end} · ${trial.attempts.length} attempt${trial.attempts.length === 1 ? "" : "s"}</span></div><span class="pill ${statusClass}">${this.escape(this.title(trial.status))}</span></article>`;
    }).join("");
    const incidents = currentAttempt?.incidents || [];
    const reviewRequired = workflow === "incident_review_required";
    const incidentPrefix = `${block.id}:${phase}:${current?.id || "none"}:${currentAttempt?.number || "none"}`;
    const incidentRows = incidents.length
      ? `<ul class="incident-list">${incidents.map((incident) => `<li data-dom-key="incident:${incidentPrefix}:${incident.id}"><span>Occurrence ${incident.id} · ${this.escape(incident.at_utc)}</span>${incident.text ? this.escape(incident.text) : "Description pending"}</li>`).join("")}</ul>`
      : '<p class="supporting-copy">No incident occurrence reported for this attempt.</p>';
    const reviewFields = incidents.map((incident) => {
      const key = `${incidentPrefix}:${incident.id}`;
      return `<label class="incident-description" data-dom-key="description:${this.escape(key)}"><span>Occurrence ${incident.id} · ${this.escape(incident.at_utc)}</span><textarea data-incident-input data-incident-id="${incident.id}" maxlength="2000" required placeholder="Describe what happened">${this.escape(incident.text || "")}</textarea></label>`;
    }).join("");
    const technicalOutcome = currentAttempt?.technical_outcome;
    const technicalLabel = technicalOutcome?.completed
      ? "Technical result: successful"
      : "Technical result: invalid";
    const incidentPanel = `<p class="supporting-copy" ${recording ? "" : "hidden"}>Click once for every observed occurrence. Descriptions will be requested when the attempt ends.</p>
      <button class="reject-button" data-incident-occurrence data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${recording ? "" : "hidden"} ${this.busy || !recording ? "disabled" : ""}>Signal incident occurrence</button>
      <div data-dom-key="incident-history" ${reviewRequired ? "hidden" : ""}>${incidentRows}</div>
      <p class="inline-warning" ${reviewRequired ? "" : "hidden"}>Describe every occurrence, then decide whether the incidents invalidate this attempt. ${this.escape(technicalLabel)}.</p>
      <form class="incident-review-form" data-trial-incident-review-form data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" ${reviewRequired ? "" : "hidden"}>${reviewFields}<div class="training-actions"><button class="secondary-button" data-incident-invalidates="false" type="submit" ${this.busy || !reviewRequired ? "disabled" : ""}>Validate attempt</button><button class="reject-button" data-incident-invalidates="true" type="submit" ${this.busy || !reviewRequired ? "disabled" : ""}>Invalidate attempt</button></div></form>`;
    const processItems = [
      ["Mode", block.mode],
      ["Stack", experiment.stack.status],
      ["Mapper", experiment.mapper.status],
      ["Recorder", experiment.recorder.status],
    ].map(([term, value]) => `<div data-dom-key="summary:${term}"><dt>${this.escape(term)}</dt><dd>${this.escape(this.title(value))}</dd></div>`).join("");
    const canPrepare = workflow === "awaiting_prepare" && !experiment.can_end;
    const mapperReady = experiment.mapper?.status === "active" && experiment.mapper?.active_mode === block.mode;
    const canStart = ["awaiting_start_pose", "ready"].includes(workflow)
      && live.start_within_thresholds
      && !experiment.go_to?.motion?.active
      && mapperReady;
    this.setClass(panel, "panel-page");
    this.patchMarkup(panel, `<section class="hero-card trial-hero-card">
        <div class="trial-hero-heading"><p class="step-number">Panel ${panelLetter} · ${title}</p><h2>${this.escape(this.title(block.mode))} ${phase}</h2><p class="supporting-copy">${completed} of ${trials.length} trials resolved · ${training.deviations?.length || 0} deviations</p></div>
        <div class="trial-hero-status"><span class="pill ${recording ? "pill--warning" : experiment.can_end ? "pill--active" : "pill--neutral"}">${this.escape(this.title(workflow))}</span><dl class="participant-summary trial-process-summary" aria-label="Process status">${processItems}</dl></div>
      </section>
      <div class="trial-workspace">
        <div class="trial-main-column">
          <section class="card"><div class="section-heading"><div><p class="step-number">Current trial</p><h2>${current ? `Trial ${current.id} · cycle ${current.cycle} · target ${current.target_start} → ${current.target_end}` : experiment.can_end ? "All trials resolved" : "Prepare the next trial"}</h2></div><span class="supporting-copy">${current ? this.escape(current.folder) : this.escape(block.folder)}</span></div>
            <div class="training-metrics"><article><span>Start linear error</span><strong>${errorValue(live.start_error, "linear_mm")}</strong></article><article><span>Start angular error</span><strong>${errorValue(live.start_error, "angular_deg")}</strong></article><article><span>Target linear error</span><strong>${errorValue(live.target_error, "linear_mm")}</strong></article><article><span>Target angular error</span><strong>${errorValue(live.target_error, "angular_deg")}</strong></article></div>
            <p class="supporting-copy">${live.error ? this.escape(live.error) : current ? `Place the robot at target_out_${current.target_start}. Arrival success is measured at target_${current.target_end}.` : "The mapper remains active between trials."}</p>
            <div class="training-actions"><button class="secondary-button" data-trial-action="prepare" data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || !canPrepare ? "disabled" : ""}>Prepare next trial and move to start</button><button class="primary-button" data-trial-action="start" data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || !canStart ? "disabled" : ""}>Start recording</button><button class="reject-button" data-trial-action="stop" data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || !recording ? "disabled" : ""}>Stop recording</button></div>
            <div class="decision-card" ${workflow === "decision_required" ? "" : "hidden"}><strong>This attempt is invalid.</strong><p>Retry the same trial or continue with a recorded protocol deviation.</p><div class="training-actions"><button class="secondary-button" data-trial-resolve="retry" data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || workflow !== "decision_required" ? "disabled" : ""}>Retry trial</button><button class="reject-button" data-trial-resolve="advance_with_deviation" data-trial-phase="${phase}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || workflow !== "decision_required" ? "disabled" : ""}>Continue with deviation</button></div></div>
            <p class="inline-error" ${block.error ? "" : "hidden"}>${this.escape(block.error || "")}</p></section>
          <section class="card"><div class="section-heading"><div><p class="step-number">Trial issue</p><h2>Incident occurrences</h2></div><span class="pill ${reviewRequired ? "pill--warning" : incidents.length ? "pill--error" : "pill--neutral"}">${incidents.length} occurrence${incidents.length === 1 ? "" : "s"}</span></div>${incidentPanel}</section>
          <section class="card"><div class="section-heading"><div><p class="step-number">Go to</p><h2>Calibrated start positioning</h2></div><span class="pill ${experiment.go_to?.motion?.active ? "pill--warning" : "pill--neutral"}">${experiment.go_to?.motion?.active ? this.escape(this.title(experiment.go_to.motion.current?.status)) : reviewRequired ? "Review required" : "Ready"}</span></div><p class="supporting-copy">Manual control is suspended during motion and restored afterwards. Target buttons use the calibrated target_out poses.</p><div class="go-to-grid"><button class="secondary-button" data-block-go-to="starting_point" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || reviewRequired || !experiment.go_to?.available || experiment.go_to?.motion?.active ? "disabled" : ""}>Go to starting point</button>${[1, 2, 3].map((target) => `<button class="secondary-button" data-block-go-to="target_out_${target}" data-block-id="${this.escape(block.id)}" type="button" ${this.busy || reviewRequired || !experiment.go_to?.available || experiment.go_to?.motion?.active ? "disabled" : ""}>Go to target ${target}</button>`).join("")}</div><button class="reject-button" data-block-go-to-stop data-block-id="${this.escape(block.id)}" type="button" ${experiment.go_to?.motion?.active ? "" : "hidden"} ${this.busy || !experiment.go_to?.motion?.active ? "disabled" : ""}>Stop motion</button></section>
        </div>
        <section class="card trial-progress-card"><div class="section-heading"><div><p class="step-number">Protocol progress</p><h2>${title} trials</h2></div><span class="pill ${experiment.can_end ? "pill--active" : "pill--neutral"}">${completed}/${trials.length}</span></div><div class="segment-list">${trialRows}</div></section>
      </div>
      <section class="validation-card"><div><p class="step-number">Complete ${phase}</p><h2>Return to the experiment sequence</h2><p class="supporting-copy">Every trial must succeed or be explicitly accepted with a deviation.</p></div><div class="validation-actions"><button class="text-button" data-restart-stack="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Restart stack</button><button class="reject-button" data-block-abort="${this.escape(block.id)}" type="button" ${this.busy ? "disabled" : ""}>Stop and return</button><button class="primary-button" data-block-end="${this.escape(block.id)}" data-phase="${phase}" type="button" ${this.busy || !experiment.can_end ? "disabled" : ""}>${phase === "recording" ? "End recordings" : "End training"}</button></div></section>`, `${experiment.participant.folder}:${block.id}:${phase}`);
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
    this.setText(pill, labels[result.status] || result.status);
    this.setClass(pill, `pill mode-status ${result.status === "passed" ? "pill--active" : result.status === "failed" ? "pill--error" : ["checking", "awaiting_joystick", "awaiting_confirmation"].includes(result.status) ? "pill--warning" : "pill--neutral"}`);
    const guidance = card.querySelector(".mode-guidance");
    this.setText(guidance, this.guidance(result));
    const start = card.querySelector(".mode-start");
    this.setText(start, result.status === "failed" ? `Retry ${MODE_LABELS[mode].toLowerCase()} mode` : `Check ${MODE_LABELS[mode].toLowerCase()} mode`);
    const anotherModeRunning = this.state.active_mode && this.state.active_mode !== mode;
    this.setDisabled(start, this.busy || this.state.stack.status !== "active" || anotherModeRunning || ["checking", "awaiting_joystick", "awaiting_confirmation", "passed"].includes(result.status));
    this.setHidden(card.querySelector(".confirmation"), result.status !== "awaiting_confirmation");
    const failures = result.evidence?.checks?.filter((check) => !check.passed) || [];
    const diagnosticList = card.querySelector(".diagnostic-list");
    this.patchMarkup(diagnosticList, failures.length
      ? `<details open><summary>${failures.length} pending diagnostic${failures.length > 1 ? "s" : ""}</summary><ul>${failures.map((item) => `<li data-dom-key="diagnostic:${this.escape(item.name)}">${this.escape(item.name)} · expected ${this.escape(JSON.stringify(item.expected))}, observed ${this.escape(JSON.stringify(item.observed))}</li>`).join("")}</ul></details>`
      : result.evidence ? `<p class="diagnostics-ok">Automatic diagnostics passed.</p>` : "");
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
    this.setText(toast, message);
    this.setClass(toast, `toast${error ? " toast--error" : ""}`);
    this.setHidden(toast, false);
    this.toastTimer = window.setTimeout(() => this.setHidden(toast, true), 4500);
  }
}

document.addEventListener("DOMContentLoaded", () => new SessionInterface());
