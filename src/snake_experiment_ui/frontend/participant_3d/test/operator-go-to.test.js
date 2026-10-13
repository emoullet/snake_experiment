import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../../snake_experiment_ui/static/session_app.js', import.meta.url), 'utf8');
const Interface = vm.runInNewContext(`${source}\nSessionInterface;`, {
  document: { addEventListener() {} },
});

test('Training and Recording display distinct timeout explanations and clear them for a new motion', () => {
  const ui = Object.create(Interface.prototype);
  ui.busy = false;
  ui.setClass = () => {};
  let markup;
  ui.patchMarkup = (_, html) => { markup = html; };
  const block = { id: 'block', mode: 'snake', folder: 'snake' };
  const experiment = {
    participant: { folder: '/session' },
    stack: { status: 'active' }, mapper: { status: 'active' },
    recorder: { status: 'inactive' },
    go_to: { motion: { active: false, current: { status: 'timed_out' } } },
  };
  for (const render of ['renderTraining', 'renderRecording']) {
    for (const [reason, explanation] of [
      ['progress_stalled', /insufficient progress towards the target/],
      ['total_timeout', /maximum total motion duration exceeded/],
      ['feedback_timeout', /no valid end-effector pose feedback/],
    ]) {
      experiment.go_to.motion.current = { status: 'timed_out', stop_reason: reason };
      ui[render]({}, block, experiment);
      assert.match(markup, explanation);
      experiment.go_to.motion.current = { status: 'moving' };
      experiment.go_to.motion.active = true;
      ui[render]({}, block, experiment);
      assert.doesNotMatch(markup, explanation);
      experiment.go_to.motion.active = false;
    }
  }
});
