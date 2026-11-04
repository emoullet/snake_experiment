import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../../snake_experiment_ui/static/session_app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../../snake_experiment_ui/templates/session_index.html', import.meta.url), 'utf8');
const active = (id) => ({ active: true, operation_id: id, phase: 'recording' });
const idle = (id) => ({ active: false, operation_id: id });
const snapshot = (status) => ({ current_view: 'G', experiment: { finalization: status } });
const flush = () => new Promise(setImmediate);

function fixture(fetch = async () => { throw new Error('offline'); }) {
  const elements = new Map(), timers = new Map();
  let nextTimer = 0;
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { hidden: true, textContent: '', inert: false });
    return elements.get(id);
  };
  const views = ['b', 'c', 'd', 'e', 'f', 'g'].map((id) => element(`view-${id}`));
  const protection = element('protection-panel');
  const document = {
    getElementById: element,
    querySelectorAll: (selector) => selector === '.view-page' ? views : [],
    addEventListener() {},
  };
  const window = {
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const Interface = vm.runInNewContext(`${source}\nSessionInterface;`, { document, window, fetch, AbortController });
  const ui = Object.create(Interface.prototype);
  ui.finalizationState = null;
  ui.finalizationEpoch = 0;
  ui.finalizationPending = false;
  ui.render = () => ui.renderFinalization();
  return { ui, Interface, element, views, protection, timers };
}

test('banner survives dynamic views, blocks workflow views and excludes protection', () => {
  const { ui, element, views, protection } = fixture();
  ui.receiveFinalization(active(1));
  assert.equal(element('acquisition-finalization').hidden, false);
  assert.equal(element('acquisition-finalization-title').textContent, 'Vérification en cours…');
  assert.ok(views.every((view) => view.inert));
  assert.equal(protection.inert, false);
  assert.match(html, /id="acquisition-finalization"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.ok(html.indexOf('id="acquisition-finalization"') < html.indexOf('id="view-b"'));
  assert.ok(html.indexOf('id="protection-panel"') < html.indexOf('id="acquisition-finalization"'));
});

test('idle lightweight status never unlocks before a fresh complete snapshot', () => {
  const { ui, element, views } = fixture();
  ui.receiveFinalization(active(1));
  ui.receiveFinalization(idle(1));
  assert.equal(element('acquisition-finalization').hidden, false);
  assert.ok(views.every((view) => view.inert));
  ui.update(snapshot(idle(1)));
  assert.equal(element('acquisition-finalization').hidden, true);
  assert.ok(views.every((view) => !view.inert));
});

test('late snapshots and statuses cannot mask a newer verification', () => {
  const { ui, element } = fixture();
  ui.receiveFinalization(active(1));
  ui.receiveFinalization(active(2));
  ui.update(snapshot(idle(1)));
  assert.equal(element('acquisition-finalization').hidden, false);
  ui.receiveFinalization(idle(2));
  ui.update(snapshot(active(2))); // stale active response after observed completion
  assert.equal(ui.finalizationState.active, false);
  assert.equal(element('acquisition-finalization').hidden, false);
  ui.update(snapshot(idle(2)));
  assert.equal(element('acquisition-finalization').hidden, true);
  ui.update(snapshot(active(1)));
  assert.equal(element('acquisition-finalization').hidden, true);
});

test('independent polling keeps working while full state refresh waits', async () => {
  const { ui, element, timers } = fixture();
  ui.receiveFinalization(active(1));
  let release, polls = 0;
  ui.finalizationFetch = (url) => {
    if (url === '/api/experiment/finalization') { polls++; return Promise.resolve(idle(1)); }
    return new Promise((resolve) => { release = resolve; });
  };
  await ui.pollFinalization();
  assert.equal(element('acquisition-finalization').hidden, false);
  assert.equal(ui.finalizationRefreshing, true);
  assert.ok([...timers.values()].some(({ delay }) => delay === 250));
  await ui.pollFinalization();
  assert.equal(polls, 2);
  release(snapshot(idle(1)));
  await flush();
  assert.equal(element('acquisition-finalization').hidden, true);
});

test('no overlapping status requests and connection loss remains blocking', async () => {
  const { ui, element, views } = fixture();
  ui.receiveFinalization(active(1));
  let reject, calls = 0;
  ui.finalizationFetch = () => { calls++; return new Promise((resolve, fail) => { reject = fail; }); };
  const request = ui.pollFinalization();
  await ui.pollFinalization();
  assert.equal(calls, 1);
  reject(new Error('offline'));
  await request;
  assert.equal(element('acquisition-finalization').hidden, false);
  assert.match(element('acquisition-finalization-title').textContent, /indisponible.*Reconnexion/);
  assert.ok(views.every((view) => view.inert));
  ui.finalizationFetch = async () => active(1);
  await ui.pollFinalization();
  assert.equal(element('acquisition-finalization-title').textContent, 'Vérification en cours…');
});

test('a newly opened page shows status before its first workflow snapshot', async () => {
  const { Interface, element } = fixture(async () => ({ ok: true, json: async () => active(4) }));
  Interface.prototype.bindActions = () => {};
  Interface.prototype.connect = () => {}; // Main channel cannot provide a snapshot yet.
  const ui = new Interface();
  await flush();
  assert.equal(ui.state, null);
  assert.equal(element('acquisition-finalization').hidden, false);
});

test('requests from an earlier backend connection are ignored', async () => {
  const { ui, element } = fixture();
  ui.receiveFinalization(active(3));
  let resolve;
  ui.finalizationFetch = () => new Promise((done) => { resolve = done; });
  const request = ui.pollFinalization();
  ui.finalizationEpoch++;
  ui.finalizationState = null;
  resolve(idle(3));
  await request;
  assert.equal(element('acquisition-finalization').hidden, false);
  ui.receiveFinalization(idle(0));
  ui.update({ current_view: 'B' });
  assert.equal(element('acquisition-finalization').hidden, true);
});
