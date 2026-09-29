(() => {
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
  const connection = document.getElementById("participant-connection");
  let reconnectDelay = 500;
  video.addEventListener("error", () => { videoError.hidden = false; });
  video.addEventListener("loadedmetadata", () => { videoError.hidden = true; });
  modeVideo.addEventListener("error", () => { modeVideoError.hidden = false; });
  modeVideo.addEventListener("loadedmetadata", () => { modeVideoError.hidden = true; });

  function render(state) {
    const showA = state.panel === "A";
    const showB = state.panel === "B" && ["baseline", "snake"].includes(state.mode);
    waiting.hidden = showA || showB;
    presentation.hidden = !showA;
    modePanel.hidden = !showB;
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
