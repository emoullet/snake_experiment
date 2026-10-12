import { MappingAnimator } from './animation.js';
import { participantMappingSelection } from './mapping.js';

const message = document.getElementById('participant-mapping-message');
const snakeIndicator = document.getElementById('participant-snake-indicator');
const animator = new MappingAnimator([
  document.getElementById('participant-horizontal-view'),
  document.getElementById('participant-vertical-view'),
], (_state, source) => {
  poseSource = source;
  updateMessage();
}, error => { message.textContent = `Mapping animation is unavailable: ${error.message || error}`; });
let latestState = null;
let lastBlock = null;
let lastKnownSubmode = null;
let illustrationNote = '';
let poseSource = null;
const viewButtons = [...document.querySelectorAll('[data-preview-view]')];

function updateViewButtons() {
  for (const button of viewButtons) {
    button.disabled = false;
    button.setAttribute('aria-pressed', String(button.dataset.previewView === animator.cameraView));
  }
}

for (const button of viewButtons) {
  button.addEventListener('click', () => {
    animator.setCameraView(button.dataset.previewView);
    updateViewButtons();
  });
}

function updateMessage() {
  if (!animator.selection) return;
  const poseText = poseSource === 'Demo pose'
    ? 'Demo pose · joint states are unavailable or stale.'
    : poseSource === 'Live pose'
      ? 'Live pose · schematic joystick inputs, not live joystick movements.'
      : 'Preparing mapping animation.';
  message.textContent = illustrationNote ? `${poseText} ${illustrationNote}` : poseText;
}

function update(state) {
  latestState = state;
  if (!animator.config) return;
  if (!['B', 'C'].includes(state?.view)) {
    animator.stop();
    animator.setSelection(null, null);
    lastBlock = null;
    lastKnownSubmode = null;
    illustrationNote = '';
    poseSource = null;
    return;
  }
  const mode = state.mode;
  const block = `${state.mode}:${state.phase}`;
  if (block !== lastBlock) {
    animator.setSelection(null, null);
    lastBlock = block;
    lastKnownSubmode = null;
    poseSource = null;
  }
  const selection = participantMappingSelection(animator.config, state, lastKnownSubmode);
  if (selection?.submodeReported) lastKnownSubmode = selection.submode;
  snakeIndicator.hidden = mode !== 'snake';
  if (mode === 'snake') {
    snakeIndicator.textContent = !selection?.snakeHeldKnown ? 'Snake button unknown · illustrating released state'
      : selection.snakeHeld ? 'Snake button held' : 'Snake button released';
    snakeIndicator.className = `pill ${!selection?.snakeHeldKnown ? 'pill--warning' : selection.snakeHeld ? 'pill--active' : 'pill--neutral'}`;
  }
  if (!selection) {
    animator.setSelection(null, null);
    illustrationNote = '';
    message.textContent = 'Mapping configuration unavailable · animations cannot start.';
  } else {
    const notes = [];
    if (!selection.submodeReported) notes.push(`Illustrative ${selection.submode} mapping; mapper sub-mode unavailable.`);
    if (!selection.snakeHeldKnown) notes.push('Snake button state unavailable; illustrating released state.');
    illustrationNote = notes.join(' ');
    animator.setSelection(selection.mode, selection.submode, selection.snakeHeld);
    updateMessage();
  }
  animator.start();
}

window.addEventListener('participant:state', event => update(event.detail));
animator.initialize().then(() => {
  updateViewButtons();
  if (latestState) update(latestState);
  else fetch('/participant/api/state').then(response => response.json()).then(update).catch(() => {});
}).catch(error => {
  message.textContent = `Mapping animation is unavailable: ${error.message || error}`;
  animator.stop();
});
