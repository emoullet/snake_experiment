import { MappingAnimator } from './animation.js';

const PREFIX = '/participant/3d-preview';
const connection = document.getElementById('preview-connection');
const message = document.getElementById('preview-message');
const modeSelect = document.getElementById('preview-mode');
const submodeSelect = document.getElementById('preview-submode');
const snakeControl = document.getElementById('preview-snake-control');
const snakeHeld = document.getElementById('preview-snake-held');
const animator = new MappingAnimator([
  document.getElementById('preview-horizontal-view'),
  document.getElementById('preview-vertical-view'),
], (state, source) => {
  setConnection(source);
  message.textContent = source === 'Live pose'
    ? state?.gripper_available
      ? 'Using the latest arm and gripper pose at each animation loop.'
      : 'Using the latest arm pose; the gripper uses its demo opening.'
    : 'Joint states are unavailable or stale. The animation uses the demo pose.';
}, error => { message.textContent = `Mapping animation is unavailable: ${error.message || error}`; });
let reconnectTimer = null;

function setConnection(status) {
  connection.textContent = status;
  connection.className = `pill ${status === 'Live pose' ? 'pill--active' : 'pill--neutral'}`;
}

function selectMapping() {
  animator.setSelection(modeSelect.value, submodeSelect.value, snakeHeld.checked);
}

function updateControls() {
  const names = Object.keys(animator.config.mapper[modeSelect.value]);
  const previous = submodeSelect.value;
  submodeSelect.replaceChildren(...names.map(name => new Option(name, name)));
  submodeSelect.value = names.includes(previous) ? previous : names[0];
  snakeControl.hidden = modeSelect.value !== 'snake';
  selectMapping();
}

function connect() {
  const socketUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${PREFIX}/ws`;
  const socket = new WebSocket(socketUrl);
  socket.onmessage = event => {
    try {
      const state = JSON.parse(event.data);
      if (state.robot === 'explorer_poc2') setConnection(state.status === 'live' ? 'Live pose' : 'Demo pose');
    } catch { /* Ignore malformed state. */ }
  };
  socket.onclose = () => {
    setConnection('Disconnected');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1500);
  };
  socket.onerror = () => socket.close();
}

async function start() {
  animator.start();
  await animator.initialize();
  updateControls();
  modeSelect.disabled = false;
  submodeSelect.disabled = false;
  snakeHeld.disabled = false;
  modeSelect.addEventListener('change', updateControls);
  submodeSelect.addEventListener('change', selectMapping);
  snakeHeld.addEventListener('change', selectMapping);
  connect();
}

start().catch(error => {
  message.textContent = `3D preview is unavailable: ${error.message || error}`;
  setConnection('Unavailable');
  animator.stop();
});
