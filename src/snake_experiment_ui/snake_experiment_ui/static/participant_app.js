(() => {
  const waiting = document.getElementById("participant-waiting");
  const presentation = document.getElementById("participant-presentation");
  const frame = document.getElementById("participant-video-frame");
  const placeholder = document.getElementById("participant-video-placeholder");
  const video = document.getElementById("participant-video");
  const videoError = document.getElementById("participant-video-error");
  const connection = document.getElementById("participant-connection");
  let reconnectDelay = 500;
  video.addEventListener("error", () => { videoError.hidden = false; });
  video.addEventListener("loadedmetadata", () => { videoError.hidden = true; });

  function render(state) {
    const show = state.panel === "A";
    if (waiting.hidden !== show) waiting.hidden = show;
    if (presentation.hidden !== !show) presentation.hidden = !show;
    const available = !!state.video_available;
    if (frame.hidden !== !available) frame.hidden = !available;
    if (placeholder.hidden !== available) placeholder.hidden = available;
    if (available && !video.getAttribute("src")) video.src = "/participant/video";
    if (!available && video.getAttribute("src")) {
      video.pause();
      video.removeAttribute("src");
      video.load();
      videoError.hidden = true;
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
