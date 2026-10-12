(() => {
  const workspace = document.querySelector(".participant-workspace");
  const waiting = document.getElementById("participant-waiting");
  const presentation = document.getElementById("participant-presentation");
  const frame = document.getElementById("participant-video-frame");
  const placeholder = document.getElementById("participant-video-placeholder");
  const video = document.getElementById("participant-video");
  const videoError = document.getElementById("participant-video-error");
  const modeView = document.getElementById("participant-mode-explanation");
  const modeTitle = document.getElementById("participant-mode-title");
  const modeFrame = document.getElementById("participant-mode-video-frame");
  const modePlaceholder = document.getElementById("participant-mode-video-placeholder");
  const modeVideo = document.getElementById("participant-mode-video");
  const modeVideoError = document.getElementById("participant-mode-video-error");
  const trialView = document.getElementById("participant-trial");
  const mappingPreview = document.getElementById("participant-mapping");
  const discoveryPreview = document.getElementById("participant-discovery-preview");
  const trialTitle = document.getElementById("participant-trial-title");
  const trialTask = document.getElementById("participant-task");
  const linearDistance = document.getElementById("participant-linear-distance");
  const angularDistance = document.getElementById("participant-angular-distance");
  const localMode = document.getElementById("participant-local-mode");
  const trialState = document.getElementById("participant-trial-state");
  const cameraVideo = document.getElementById("participant-camera-video");
  const cameraPlaceholder = document.getElementById("participant-camera-placeholder");
  const cameraRetry = document.getElementById("participant-camera-retry");
  const cameraError = document.getElementById("participant-camera-error");
  const soundRetry = document.getElementById("participant-sound-retry");
  // Local copy of https://www.myinstants.com/media/sounds/mario_coin_sound.mp3.
  const successSound = new Audio("/session-static/mario_coin_sound.mp3");
  successSound.preload = "auto";
  const connection = document.getElementById("participant-connection");
  const overloadBanner = document.getElementById("participant-overload-warning");
  const overloadMessage = document.getElementById("participant-overload-message");
  const overloadMessages = {
    retreating: "Force overload detected. Commands paused. Automatic retreat in progress.",
    waiting_clear: "Commands paused. Retreat paused while contact force clears.",
    retreat_complete: "Retreat complete. Commands remain paused. Please wait for the experimenter.",
    paused: "Force overload detected. Commands paused. Please wait for the experimenter.",
    unavailable: "Commands paused. Please wait for the experimenter.",
  };
  let reconnectDelay = 500;
  let cameraStream = null;
  let cameraRequest = 0;
  let cameraStarting = false;
  let currentView = "waiting";
  let successEventInitialized = false;
  let lastSuccessEventId = null;
  let receivedWebSocketState = false;
  const setText = (element, value) => { if (element.textContent !== value) element.textContent = value; };
  video.addEventListener("error", () => { videoError.hidden = false; });
  video.addEventListener("loadedmetadata", () => { videoError.hidden = true; });
  modeVideo.addEventListener("error", () => { modeVideoError.hidden = false; });
  modeVideo.addEventListener("loadedmetadata", () => { modeVideoError.hidden = true; });

  function videoPlayback(player, warning, mediaError) {
    const handled = new Set();
    let current = null;
    let metadataListener = null;
    function cancel() {
      if (metadataListener) player.removeEventListener("loadedmetadata", metadataListener);
      metadataListener = null;
      if (current) player.pause();
      current = null;
      warning.hidden = true;
    }
    function start(request) {
      if (current !== request || player.getAttribute("src") !== request.source) return;
      metadataListener = null;
      try {
        player.currentTime = 0;
        Promise.resolve(player.play()).catch((error) => {
          if (current !== request || error.name === "AbortError") return;
          if (error.name === "NotAllowedError") warning.hidden = false;
          else mediaError.hidden = false;
        });
      } catch {
        if (current === request) mediaError.hidden = false;
      }
    }
    player.addEventListener("play", () => { warning.hidden = true; });
    player.addEventListener("error", cancel);
    window.addEventListener("pagehide", cancel);
    return (requestId, visible) => {
      const source = player.getAttribute("src");
      if (visible && requestId && current?.id === requestId && current.source === source) return;
      cancel();
      if (!visible || !requestId || !source || handled.has(requestId)) return;
      handled.add(requestId);
      current = { id: requestId, source };
      const request = current;
      if (player.readyState >= 1) start(request);
      else {
        metadataListener = () => start(request);
        player.addEventListener("loadedmetadata", metadataListener, { once: true });
      }
    };
  }
  const presentationPlayback = videoPlayback(video, document.getElementById("participant-video-autoplay-warning"), videoError);
  const explanationPlayback = videoPlayback(modeVideo, document.getElementById("participant-mode-video-autoplay-warning"), modeVideoError);

  function stopCamera() {
    cameraRequest += 1;
    cameraStarting = false;
    if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    cameraVideo.srcObject = null;
    cameraVideo.hidden = true;
    cameraPlaceholder.hidden = false;
    setText(cameraPlaceholder, "Camera preview is off.");
    cameraRetry.hidden = true;
    cameraRetry.disabled = false;
    cameraError.hidden = true;
  }

  async function startCamera() {
    if (currentView !== "C" || cameraStream || cameraStarting) return;
    const request = ++cameraRequest;
    cameraStarting = true;
    cameraRetry.hidden = true;
    cameraError.hidden = true;
    setText(cameraPlaceholder, "Waiting for camera access…");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is unavailable in this browser or origin.");
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      if (request !== cameraRequest || currentView !== "C") {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      cameraStarting = false;
      cameraStream = stream;
      cameraVideo.srcObject = stream;
      cameraVideo.hidden = false;
      cameraPlaceholder.hidden = true;
    } catch (error) {
      if (request !== cameraRequest) return;
      cameraStarting = false;
      setText(cameraPlaceholder, "Camera preview is unavailable.");
      setText(cameraError, "Camera preview is unavailable. You may continue without it.");
      cameraError.hidden = false;
      cameraRetry.hidden = false;
    }
  }

  cameraRetry.addEventListener("click", startCamera);
  window.addEventListener("pagehide", stopCamera);

  async function playSuccessSound() {
    try {
      successSound.currentTime = 0;
      await successSound.play();
      soundRetry.hidden = true;
    } catch {
      // Audible autoplay can be blocked until the participant interacts with the page.
      if (currentView === "C") soundRetry.hidden = false;
    }
  }

  soundRetry.addEventListener("click", () => { void playSuccessSound(); });

  function render(state) {
    const warning = overloadMessages[state.overload_warning?.phase] || "";
    setText(overloadMessage, warning);
    overloadBanner.hidden = !warning;
    const showA = state.view === "A";
    const showB = state.view === "B" && ["baseline", "snake"].includes(state.mode);
    const showC = state.view === "C" && ["baseline", "snake"].includes(state.mode);
    const enteringC = showC && currentView !== "C";
    if (!showC) {
      successEventInitialized = false;
      lastSuccessEventId = null;
      soundRetry.hidden = true;
      if (currentView === "C") {
        successSound.pause();
        successSound.currentTime = 0;
      }
    } else if (!successEventInitialized) {
      // A page reload or resumed block must not replay an earlier success.
      successEventInitialized = true;
      lastSuccessEventId = state.success_event_id || null;
    } else if (state.success_event_id && state.success_event_id !== lastSuccessEventId) {
      lastSuccessEventId = state.success_event_id;
      void playSuccessSound();
    }
    workspace.classList.toggle("participant-workspace--trial", showC);
    if (!showC && currentView === "C") stopCamera();
    currentView = showC ? "C" : state.view;
    waiting.hidden = showA || showB || showC;
    presentation.hidden = !showA;
    modeView.hidden = !showB;
    trialView.hidden = !showC;
    const previewParent = showB ? discoveryPreview : trialView;
    if (mappingPreview.parentElement !== previewParent) previewParent.appendChild(mappingPreview);
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
    presentationPlayback(state.video_play_request_id, showA && state.video_available);
    explanationPlayback(state.video_play_request_id, showB && state.video_available);
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
    }
    if (enteringC) void startCamera();
    window.dispatchEvent(new CustomEvent("participant:state", { detail: state }));
  }

  function connect() {
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${window.location.host}/participant/ws`);
    socket.onopen = () => { reconnectDelay = 500; connection.textContent = "Connected"; connection.className = "pill pill--active"; };
    socket.onmessage = (event) => {
      receivedWebSocketState = true;
      render(JSON.parse(event.data));
    };
    socket.onclose = () => {
      connection.textContent = "Reconnecting";
      connection.className = "pill pill--warning";
      if (!overloadBanner.hidden) setText(overloadMessage, overloadMessages.unavailable);
      window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }
  fetch("/participant/api/state").then((response) => response.json()).then((state) => {
    if (!receivedWebSocketState) render(state);
  }).catch(() => {});
  connect();
})();
