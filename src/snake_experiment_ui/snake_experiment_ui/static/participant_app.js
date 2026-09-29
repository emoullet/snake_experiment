(() => {
  const workspace = document.querySelector(".participant-workspace");
  const waiting = document.getElementById("participant-waiting");
  const presentation = document.getElementById("participant-presentation");
  const frame = document.getElementById("participant-video-frame");
  const placeholder = document.getElementById("participant-video-placeholder");
  const video = document.getElementById("participant-video");
  const videoError = document.getElementById("participant-video-error");
  const modePanel = document.getElementById("participant-mode-explanation");
  const modeTitle = document.getElementById("participant-mode-title");
  const modeFrame = document.getElementById("participant-mode-video-frame");
  const modePlaceholder = document.getElementById("participant-mode-video-placeholder");
  const modeVideo = document.getElementById("participant-mode-video");
  const modeVideoError = document.getElementById("participant-mode-video-error");
  const trialPanel = document.getElementById("participant-trial");
  const trialTitle = document.getElementById("participant-trial-title");
  const trialTask = document.getElementById("participant-task");
  const linearDistance = document.getElementById("participant-linear-distance");
  const angularDistance = document.getElementById("participant-angular-distance");
  const localMode = document.getElementById("participant-local-mode");
  const trialState = document.getElementById("participant-trial-state");
  const stateImage = document.getElementById("participant-state-image");
  const stateImagePlaceholder = document.getElementById("participant-state-image-placeholder");
  const cameraVideo = document.getElementById("participant-camera-video");
  const cameraPlaceholder = document.getElementById("participant-camera-placeholder");
  const cameraEnable = document.getElementById("participant-camera-enable");
  const cameraError = document.getElementById("participant-camera-error");
  const connection = document.getElementById("participant-connection");
  let reconnectDelay = 500;
  let cameraStream = null;
  let cameraRequest = 0;
  let currentPanel = "waiting";
  const setText = (element, value) => { if (element.textContent !== value) element.textContent = value; };
  video.addEventListener("error", () => { videoError.hidden = false; });
  video.addEventListener("loadedmetadata", () => { videoError.hidden = true; });
  modeVideo.addEventListener("error", () => { modeVideoError.hidden = false; });
  modeVideo.addEventListener("loadedmetadata", () => { modeVideoError.hidden = true; });
  stateImage.addEventListener("error", () => {
    stateImage.hidden = true;
    stateImagePlaceholder.hidden = false;
  });

  function stopCamera() {
    cameraRequest += 1;
    if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    cameraVideo.srcObject = null;
    cameraVideo.hidden = true;
    cameraPlaceholder.hidden = false;
    cameraEnable.hidden = false;
    cameraEnable.disabled = false;
  }

  cameraEnable.addEventListener("click", async () => {
    if (currentPanel !== "C" || cameraStream) return;
    const request = ++cameraRequest;
    cameraEnable.disabled = true;
    cameraError.hidden = true;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is unavailable in this browser or origin.");
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      if (request !== cameraRequest || currentPanel !== "C") {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      cameraStream = stream;
      cameraVideo.srcObject = stream;
      cameraVideo.hidden = false;
      cameraPlaceholder.hidden = true;
      cameraEnable.hidden = true;
    } catch (error) {
      if (request !== cameraRequest) return;
      setText(cameraError, "Camera preview is unavailable. You may continue without it.");
      cameraError.hidden = false;
      cameraEnable.disabled = false;
    }
  });
  window.addEventListener("pagehide", stopCamera);

  function render(state) {
    const showA = state.panel === "A";
    const showB = state.panel === "B" && ["baseline", "snake"].includes(state.mode);
    const showC = state.panel === "C" && ["baseline", "snake"].includes(state.mode);
    workspace.classList.toggle("participant-workspace--trial", showC);
    if (!showC && currentPanel === "C") stopCamera();
    currentPanel = showC ? "C" : state.panel;
    waiting.hidden = showA || showB || showC;
    presentation.hidden = !showA;
    modePanel.hidden = !showB;
    trialPanel.hidden = !showC;
    if (!showA && !video.paused) video.pause();
    if (!showB && !modeVideo.paused) modeVideo.pause();
    if (showA) {
      const available = !!state.video_available;
      frame.hidden = !available;
      placeholder.hidden = available;
      if (available && !video.getAttribute("src")) video.src = "/participant/video";
    }
    if (showA && !state.video_available && video.getAttribute("src")) {
      video.pause();
      video.removeAttribute("src");
      video.load();
      videoError.hidden = true;
    }
    if (showB) {
      const label = state.mode === "baseline" ? "Baseline" : "Snake";
      if (modeTitle.textContent !== `${label} mode explanation`) modeTitle.textContent = `${label} mode explanation`;
      const available = !!state.video_available;
      modeFrame.hidden = !available;
      modePlaceholder.hidden = available;
      const source = `/participant/video/mode/${state.mode}`;
      if (available && modeVideo.getAttribute("src") !== source) {
        modeVideo.src = source;
        modeVideoError.hidden = true;
      }
      if (!available && modeVideo.getAttribute("src")) {
        modeVideo.pause();
        modeVideo.removeAttribute("src");
        modeVideo.load();
        modeVideoError.hidden = true;
      }
    }
    if (showC) {
      const modeName = state.mode === "baseline" ? "Baseline" : "Snake";
      const phaseName = state.phase === "recording" ? "recording" : "training";
      setText(trialTitle, `${modeName} ${phaseName}`);
      setText(trialTask, state.task || "Waiting for next trial");
      setText(linearDistance, Number.isFinite(state.linear_mm) ? `${state.linear_mm.toFixed(1)} mm` : "—");
      setText(angularDistance, Number.isFinite(state.angular_deg) ? `${state.angular_deg.toFixed(1)}°` : "—");
      setText(localMode, state.local_mode || "Unknown");
      setText(trialState, state.state || "Waiting");
      const stateClass = state.state === "Target reached" ? "pill pill--active" : state.state === "Recording" ? "pill pill--warning" : "pill pill--neutral";
      if (trialState.className !== stateClass) trialState.className = stateClass;
      const source = state.local_mode && state.state_image_available
        ? `/participant/state-image/${state.mode}/${state.local_mode}` : null;
      if (source && stateImage.getAttribute("src") !== source) {
        stateImage.src = source;
        stateImage.hidden = false;
        stateImagePlaceholder.hidden = true;
      } else if (!source && stateImage.getAttribute("src")) {
        stateImage.removeAttribute("src");
        stateImage.hidden = true;
        stateImagePlaceholder.hidden = false;
      }
    }
  }

  function connect() {
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${window.location.host}/participant/ws`);
    socket.onopen = () => { reconnectDelay = 500; connection.textContent = "Connected"; connection.className = "pill pill--active"; };
    socket.onmessage = (event) => render(JSON.parse(event.data));
    socket.onclose = () => {
      connection.textContent = "Reconnecting";
      connection.className = "pill pill--warning";
      window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }
  fetch("/participant/api/state").then((response) => response.json()).then(render).catch(() => {});
  connect();
})();
