import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../../snake_experiment_ui/static/session_app.js', import.meta.url), 'utf8');

function interfaceFor(view = 'E') {
  const elements = new Map();
  const control = { disabled: false };
  const document = {
    addEventListener() {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { hidden: true, disabled: false, textContent: '' });
      return elements.get(id);
    },
    querySelectorAll() { return [control]; },
  };
  const Interface = vm.runInNewContext(`${source}\nSessionInterface;`, { document });
  const ui = Object.create(Interface.prototype);
  ui.busy = false;
  ui.state = {
    current_view: view, stack: { status: 'inactive' },
    experiment: { stack: { status: 'active' } },
    protection: { state: 'translating', blocked: true, fresh: true, rearm_ready: false,
      configuration: 'ready', cleanup_pending: false, target: 'target_out_1', reason: 'overload' },
  };
  return { ui, elements, control };
}

test('shared protection panel uses active block stack on Discovery, Training and Recording', () => {
  for (const view of ['E', 'F', 'G']) {
    const { ui, elements, control } = interfaceFor(view);
    for (const phase of ['waiting_retreat', 'translating', 'waiting_clear', 'rotating', 'locked']) {
      ui.state.protection.state = phase;
      ui.renderProtection();
      assert.equal(elements.get('protection-panel').hidden, false);
      assert.equal(elements.get('protection-stop').disabled, phase === 'locked');
      assert.equal(elements.get('protection-rearm').disabled, true);
      assert.match(elements.get('protection-message').textContent, /overload.*target_out_1/);
      assert.equal(control.disabled, true);
    }
    const panel = elements.get('protection-panel');
    ui.state.protection.rearm_ready = true;
    ui.renderProtection();
    assert.equal(elements.get('protection-rearm').disabled, false);
    for (const changes of [{ fresh: false }, { cleanup_pending: true }]) {
      const prior = { ...ui.state.protection };
      Object.assign(ui.state.protection, changes);
      ui.renderProtection();
      assert.equal(elements.get('protection-rearm').disabled, true);
      ui.state.protection = prior;
    }
    ui.state.protection.blocked = false;
    ui.state.protection.state = 'normal';
    ui.renderProtection();
    assert.equal(panel.hidden, true);
    assert.equal(elements.get('protection-panel'), panel);
    ui.state.experiment.stack.status = 'inactive';
    ui.state.stack.status = 'active';
    ui.state.protection.blocked = true;
    ui.renderProtection();
    assert.equal(panel.hidden, true, 'Must not use the old check-up stack for an inactive block');
  }
});

test('Discovery shows manual recovery guidance for both modes without restoring control', () => {
  for (const mode of ['baseline', 'snake']) {
    const { ui } = interfaceFor();
    ui.setClass = () => {};
    let markup;
    ui.patchMarkup = (_, html) => { markup = html; };
    const block = { id: 'discovery', mode, folder: `${mode}_discovery`,
      settings: { rosbag_topics: ['/joy', '/ee_pose', '/joint_states'] },
      mode_explanation: { status: 'completed' }, protection_interruption: { event: 1 },
      error: 'Effort overload: user control remains locked until explicit rearm.' };
    const experiment = { participant: { folder: '/session' }, stack: { status: 'active' },
      mapper: { status: 'inactive' }, recorder: { status: 'inactive' }, control_active: false };
    ui.renderDiscovery({}, block, experiment);
    assert.match(markup, /Use Rearm protection above when available/);
    Object.assign(ui.state.protection, { state: 'normal', blocked: false });
    ui.renderDiscovery({}, block, experiment);
    assert.match(markup, /Protection is clear\. Activate mode and recording manually/);
    assert.doesNotMatch(markup, /user control remains locked until explicit rearm/);
    assert.equal(experiment.control_active, false);
    block.error = 'Unrelated recorder error';
    ui.renderDiscovery({}, block, experiment);
    assert.match(markup, /Unrelated recorder error/);
    experiment.control_active = true;
    ui.renderDiscovery({}, block, experiment);
    assert.match(markup, /data-dom-key="discovery-protection-recovery" hidden/);
    assert.match(markup, /Deactivate mode and recording/);
  }
});
