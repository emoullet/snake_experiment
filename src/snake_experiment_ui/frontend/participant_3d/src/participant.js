/** Connect the read-only mapping animation to the participant session view. */

import { MappingAnimator } from './animation.js';
import { participantMappingSelection, participantSubmodeLabel } from './mapping.js';

const t = value => window.participantI18n?.t(value) ?? value;
const preview = document.getElementById('participant-mapping');
const message = document.getElementById('participant-mapping-message');
const snakeIndicator = document.getElementById('participant-snake-indicator');
const animator = new MappingAnimator([
  document.getElementById('participant-horizontal-view'),
  document.getElementById('participant-vertical-view'),
], (_state, source) => {
  poseSource = source;
  updateMessage();
}, error => { message.textContent = t(`Mapping animation is unavailable: ${error.message || error}`); });
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
      ? ''
      : 'Preparing mapping animation.';
  message.textContent = [poseText, illustrationNote].filter(Boolean).map(t).join(' ');
}

function update(state) {
  latestState = state;
  preview.hidden = state?.joystick_input_active === true || (['B', 'C'].includes(state?.view) && state?.mapping_description === 'text');
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
    snakeIndicator.textContent = t(state.snake_button_held == null ? 'Coupling unknown' : selection?.snakeHeld ? 'Coupling active' : 'Coupling inactive');
    snakeIndicator.className = `pill ${selection?.snakeHeld ? 'pill--active' : 'pill--neutral'}`;
  }
  if (!selection) {
    animator.setSelection(null, null);
    illustrationNote = '';
    message.textContent = t('Mapping configuration unavailable · animations cannot start.');
  } else {
    const notes = [];
    if (!selection.submodeReported) {
      notes.push(`Illustrative ${participantSubmodeLabel(mode, selection.submode)} mapping; mapper sub-mode unavailable.`);
    }
    illustrationNote = notes.join(' ');
    animator.setSelection(selection.mode, selection.submode, selection.snakeHeld);
    updateMessage();
  }
  if (preview.hidden) animator.stop();
  else animator.start();
}

window.addEventListener('participant:state', event => update(event.detail));
window.addEventListener('participant:language', () => {
  if (animator.config) animator.refreshLanguage();
  if (latestState) update(latestState);
});
animator.initialize().then(() => {
  updateViewButtons();
  if (latestState) update(latestState);
  else fetch('/participant/api/state').then(response => response.json()).then(update).catch(() => {});
}).catch(error => {
  message.textContent = t(`Mapping animation is unavailable: ${error.message || error}`);
  animator.stop();
});
