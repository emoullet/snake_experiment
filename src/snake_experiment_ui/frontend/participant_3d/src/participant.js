import { MappingAnimator } from './animation.js';

const message = document.getElementById('participant-mapping-message');
const snakeIndicator = document.getElementById('participant-snake-indicator');
const animator = new MappingAnimator([
  document.getElementById('participant-horizontal-view'),
  document.getElementById('participant-vertical-view'),
], (_state, source) => {
  if (animator.selection) message.textContent = source === 'Demo pose'
    ? 'Demo pose · joint states are unavailable or stale.'
    : 'Live pose · schematic joystick inputs, not live joystick movements.';
}, error => { message.textContent = `Mapping animation is unavailable: ${error.message || error}`; });
let latestState = null;
let lastBlock = null;

function update(state) {
  latestState = state;
  if (!animator.config) return;
  if (state?.panel !== 'C') {
    animator.stop();
    animator.setSelection(null, null);
    lastBlock = null;
    return;
  }
  const mode = state.mode;
  const submode = state.local_mode;
  const held = mode === 'snake'
    ? typeof state.snake_button_held === 'boolean' ? state.snake_button_held : null
    : false;
  const block = `${state.mode}:${state.phase}`;
  if (block !== lastBlock) {
    animator.setSelection(null, null);
    lastBlock = block;
  }
  snakeIndicator.hidden = mode !== 'snake';
  if (mode === 'snake') {
    snakeIndicator.textContent = held === null ? 'Snake button unknown'
      : held ? 'Snake button held' : 'Snake button released';
    snakeIndicator.className = `pill ${held === null ? 'pill--warning' : held ? 'pill--active' : 'pill--neutral'}`;
  }
  if (!submode || !animator.config.mapper[mode]?.[submode]) {
    animator.setSelection(null, null);
    message.textContent = 'Mapper sub-mode unknown · animations paused at neutral.';
  } else if (held === null) {
    animator.setSelection(null, null);
    message.textContent = 'Snake button state unknown · animations paused at neutral.';
  } else {
    animator.setSelection(mode, submode, held);
  }
  animator.start();
}

window.addEventListener('participant:state', event => update(event.detail));
animator.initialize().then(() => {
  if (latestState) update(latestState);
  else fetch('/participant/api/state').then(response => response.json()).then(update).catch(() => {});
}).catch(error => {
  message.textContent = `Mapping animation is unavailable: ${error.message || error}`;
  animator.stop();
});
