import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../../snake_experiment_ui/static/session_app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../../snake_experiment_ui/templates/session_index.html', import.meta.url), 'utf8');

function interfaceFor() {
  const elements = new Map(), handlers = new Map();
  const document = {
    addEventListener(name, handler) { handlers.set(name, handler); },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { textContent: '', className: '', disabled: true,
        hidden: true, classList: { toggle() {} }, addEventListener() {} });
      return elements.get(id);
    },
    querySelectorAll(selector) {
      if (selector.includes('data-experiment-stack')) return [document.getElementById('experiment-stack-restart')];
      return [];
    },
  };
  const Interface = vm.runInNewContext(`${source}\nSessionInterface;`, { document });
  const ui = Object.create(Interface.prototype);
  ui.busy = false;
  ui.patchMarkup = (element, markup) => { element.markup = markup; };
  ui.state = { current_view: 'D', stack: { status: 'inactive' }, experiment: {
    participant: { experimental_plan: 'snake->baseline' }, profile: { sha256: '123456789abcdef' },
    progress: { workflow: 'ready', blocks: [], warnings: [] },
    stack: { status: 'inactive', owned: false }, mapper: { status: 'inactive' },
    can_start_stack: true, can_stop_stack: false, can_restart_stack: false,
  } };
  return { ui, elements, handlers };
}

test('Experiment stack buttons stay stable and follow permissions and busy state', () => {
  const { ui, elements } = interfaceFor();
  ui.renderExperiment();
  const start = elements.get('experiment-stack-start');
  const stop = elements.get('experiment-stack-stop');
  const restart = elements.get('experiment-stack-restart');
  assert.equal(start.disabled, false);
  assert.equal(stop.disabled, true);
  assert.equal(restart.disabled, true);
  Object.assign(ui.state.experiment, { can_start_stack: false, can_stop_stack: true, can_restart_stack: true });
  for (let update = 0; update < 5; update++) ui.renderExperiment();
  assert.equal(start.disabled, true);
  assert.equal(stop.disabled, false);
  assert.equal(restart.disabled, false);
  assert.equal(elements.get('experiment-stack-start'), start);
  assert.equal(elements.get('experiment-stack-stop'), stop);
  assert.equal(elements.get('experiment-stack-restart'), restart);
  ui.busy = true;
  ui.renderExperiment();
  assert.ok(start.disabled && stop.disabled && restart.disabled);
  for (const command of ['start', 'stop', 'restart']) {
    assert.match(html, new RegExp(`id="experiment-stack-${command}"[^>]*data-experiment-stack="${command}"`));
  }
});

test('Experiment stack actions dispatch independently from panel B and block restarts', async () => {
  const { ui, handlers } = interfaceFor();
  const requests = [];
  ui.post = path => { requests.push(path); };
  ui.bindActions();
  for (const command of ['start', 'stop', 'restart']) {
    await handlers.get('click')({ target: {
      closest(selector) { return selector === '[data-experiment-stack]' ? { dataset: { experimentStack: command } } : null; },
    } });
  }
  assert.deepEqual(requests, ['/api/experiment/stack/start', '/api/experiment/stack/stop', '/api/experiment/stack/restart']);
});

test('Experiment displays graph wait in stable DOM and clears it after completion', () => {
  const { ui, elements } = interfaceFor();
  ui.state.experiment.stack_operation = { action: 'start', phase: 'waiting_for_graph' };
  ui.state.experiment.can_start_stack = false;
  ui.renderExperiment();
  const status = elements.get('experiment-stack-operation');
  assert.equal(status.textContent, 'Waiting for ROS graph cleanup');
  assert.equal(status.hidden, false);
  assert.equal(elements.get('experiment-stack-start').disabled, true);
  ui.state.experiment.stack_operation.phase = 'starting';
  ui.renderExperiment();
  assert.equal(elements.get('experiment-stack-operation'), status);
  assert.equal(status.textContent, 'Starting stack');
  ui.state.experiment.stack_operation = null;
  ui.state.experiment.can_start_stack = true;
  ui.renderExperiment();
  assert.equal(status.hidden, true);
  assert.equal(elements.get('experiment-stack-start').disabled, false);
});

test('Experiment protection panel follows experiment stack without disabling safe stop', () => {
  const { ui, elements } = interfaceFor();
  ui.state.experiment.stack.status = 'active';
  ui.state.experiment.can_start_stack = false;
  ui.state.experiment.can_stop_stack = true;
  ui.state.experiment.can_restart_stack = true; // A newer protection state still overrides stale flags.
  ui.state.protection = { state: 'locked', fresh: true, blocked: true, configuration: 'ready', rearm_ready: false };
  ui.renderExperiment();
  ui.renderProtection();
  assert.equal(elements.get('protection-panel').hidden, false);
  assert.equal(elements.get('experiment-stack-stop').disabled, false);
  assert.equal(elements.get('experiment-stack-restart').disabled, true);
});
