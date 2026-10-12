import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const script = readFileSync(new URL('../../../snake_experiment_ui/static/participant_app.js', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element extends EventTarget {
  constructor() {
    super();
    this.hidden = true;
    this.textContent = '';
    this.classList = { toggle() {} };
    this.attributes = {};
    this.readyState = 0;
    this.currentTime = 0;
    this.paused = true;
    this.muted = false;
    this.playCalls = [];
    this.pauseCalls = 0;
    this.sourceAssignments = 0;
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  set src(value) { this.attributes.src = value; this.readyState = 0; this.sourceAssignments++; }
  appendChild(element) { element.parentElement = this; }
  load() { this.readyState = 0; }
  pause() { this.paused = true; this.pauseCalls++; }
  play() {
    this.playCalls.push({ time: this.currentTime, source: this.getAttribute('src') });
    if (this.playResult) return this.playResult();
    this.paused = false;
    this.dispatchEvent(new Event('play'));
    return Promise.resolve();
  }
  loaded() { this.readyState = 1; this.dispatchEvent(new Event('loadedmetadata')); }
}

async function client(initial = { view: 'waiting' }, delayedInitial = null, cameraProvider = null) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  element('participant-mapping').parentElement = element('participant-trial');
  let socket;
  const window = new EventTarget();
  window.location = { protocol: 'http:', host: 'test' };
  window.setTimeout = () => {};
  vm.runInNewContext(script, {
    document: { getElementById: element, querySelector: element }, window,
    navigator: { mediaDevices: cameraProvider ? { getUserMedia: cameraProvider } : undefined },
    Audio: Element,
    WebSocket: class { constructor() { socket = this; } },
    CustomEvent: class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } },
    fetch: async () => ({ json: async () => delayedInitial || initial }),
  });
  await flush();
  return {
    element, send: state => socket.onmessage({ data: JSON.stringify(state) }),
    disconnect: () => socket.onclose(),
  };
}

const videoState = (id, view = 'A', mode = null, available = true) => ({
  view, mode, video_available: available, video_play_request_id: id,
});

test('display starts all three videos from zero, while duplicate snapshots never restart them', async () => {
  for (const [view, mode] of [['A', null], ['B', 'baseline'], ['B', 'snake']]) {
    const c = await client(videoState('request-1', view, mode));
    const player = c.element(view === 'A' ? 'participant-video' : 'participant-mode-video');
    assert.equal(player.playCalls.length, 0);
    player.loaded();
    assert.equal(player.playCalls.length, 1);
    assert.equal(player.playCalls[0].time, 0);
    player.currentTime = 12;
    player.pause();
    c.send(videoState('request-1', view, mode));
    assert.equal(player.playCalls.length, 1);
    assert.equal(player.currentTime, 12);
    c.send(videoState('request-2', view, mode));
    assert.equal(player.playCalls.length, 2);
    assert.equal(player.currentTime, 0);
    player.currentTime = 20;
    player.paused = true; // Ended video.
    c.send(videoState('request-3', view, mode));
    assert.equal(player.playCalls.length, 3);
    assert.equal(player.currentTime, 0);
    assert.equal(player.sourceAssignments, 1);
  }
});

test('delayed metadata uses only the latest request and cannot start a hidden video', async () => {
  const c = await client();
  const player = c.element('participant-mode-video');
  c.send(videoState('request-1', 'B', 'baseline'));
  c.send(videoState('request-2', 'B', 'snake'));
  player.loaded();
  assert.deepEqual(player.playCalls, [{ time: 0, source: '/participant/video/mode/snake' }]);
  c.send(videoState('request-3', 'B', 'baseline'));
  c.send({ view: 'waiting' });
  player.loaded();
  assert.equal(player.playCalls.length, 1);
  assert.equal(player.paused, true);
});

test('missing video stays a placeholder and a later available file can process the request', async () => {
  const c = await client(videoState('request-1', 'A', null, false));
  const player = c.element('participant-video');
  assert.equal(player.getAttribute('src'), null);
  assert.equal(c.element('participant-video-placeholder').hidden, false);
  assert.equal(player.playCalls.length, 0);
  c.send(videoState('request-1'));
  player.loaded();
  assert.equal(player.playCalls.length, 1);
});

test('a late initial HTTP snapshot cannot cancel a newer WebSocket playback request', async () => {
  let resolveInitial;
  const c = await client(null, new Promise(resolve => { resolveInitial = resolve; }));
  c.send(videoState('request-1'));
  const player = c.element('participant-video');
  player.loaded();
  player.currentTime = 8;
  resolveInitial({ view: 'waiting' });
  await flush();
  assert.equal(player.currentTime, 8);
  assert.equal(player.paused, false);
  assert.equal(c.element('participant-presentation').hidden, false);
});

test('autoplay rejection keeps sound enabled and manual Play clears its warning', async () => {
  const c = await client();
  const player = c.element('participant-video');
  const warning = c.element('participant-video-autoplay-warning');
  player.playResult = () => Promise.reject(Object.assign(new Error('Blocked'), { name: 'NotAllowedError' }));
  c.send(videoState('request-1'));
  player.loaded();
  await flush();
  assert.equal(warning.hidden, false);
  assert.equal(player.muted, false);
  c.send(videoState('request-1'));
  assert.equal(player.playCalls.length, 1);
  assert.equal(warning.hidden, false);
  player.playResult = null;
  await player.play();
  assert.equal(warning.hidden, true);
});

test('late play promise rejections from a cancelled request do not affect the new panel', async () => {
  const c = await client();
  const player = c.element('participant-video');
  let rejectOld;
  player.playResult = () => new Promise((_, reject) => { rejectOld = reject; });
  c.send(videoState('request-1'));
  player.loaded();
  c.send({ view: 'waiting' });
  rejectOld(Object.assign(new Error('Blocked'), { name: 'NotAllowedError' }));
  await flush();
  assert.equal(c.element('participant-video-autoplay-warning').hidden, true);
  assert.equal(player.paused, true);
  const reloaded = await client(videoState('request-1'));
  reloaded.element('participant-video').loaded();
  assert.equal(reloaded.element('participant-video').playCalls.length, 1);
});

test('overload banner updates on all screens without replacing nodes or restarting videos', async () => {
  for (const [view, mode] of [['A', null], ['B', 'snake'], ['C', 'baseline'], ['waiting', null]]) {
    const state = videoState('video-1', view, mode);
    const c = await client(state);
    const banner = c.element('participant-overload-warning');
    const message = c.element('participant-overload-message');
    const player = c.element(view === 'A' ? 'participant-video' : 'participant-mode-video');
    if (view === 'A' || view === 'B') player.loaded();
    player.currentTime = 7;
    const plays = player.playCalls.length;
    for (const [phase, expected] of [
      ['retreating', 'Automatic retreat in progress.'],
      ['waiting_clear', 'Retreat paused while contact force clears.'],
      ['retreat_complete', 'Retreat complete.'],
      ['paused', 'Force overload detected.'],
      ['unavailable', 'Commands paused. Please wait for the experimenter.'],
    ]) {
      c.send({ ...state, overload_warning: { phase } });
      assert.equal(banner.hidden, false);
      assert.ok(message.textContent.includes(expected));
      assert.equal(c.element('participant-overload-warning'), banner);
      assert.equal(c.element('participant-overload-message'), message);
      assert.equal(player.playCalls.length, plays);
      assert.equal(player.currentTime, 7);
    }
    c.send({ ...state, overload_warning: null });
    assert.equal(banner.hidden, true);
  }
});

test('overload warning leaves the active webcam stream running', async () => {
  let starts = 0;
  let stops = 0;
  const stream = { getTracks: () => [{ stop: () => { stops++; } }] };
  const state = { view: 'C', mode: 'baseline', phase: 'training' };
  const c = await client(state, null, async () => { starts++; return stream; });
  assert.equal(c.element('participant-camera-video').srcObject, stream);
  c.send({ ...state, overload_warning: { phase: 'retreating' } });
  c.send({ ...state, overload_warning: { phase: 'retreat_complete' } });
  assert.equal(c.element('participant-camera-video').srcObject, stream);
  assert.equal(starts, 1);
  assert.equal(stops, 0);
});

test('disconnection stops announcing movement only when an overload warning already exists', async () => {
  const c = await client({ view: 'waiting', overload_warning: { phase: 'retreating' } });
  c.disconnect();
  assert.equal(c.element('participant-overload-warning').hidden, false);
  assert.equal(c.element('participant-overload-message').textContent, 'Commands paused. Please wait for the experimenter.');
  const normal = await client({ view: 'waiting', overload_warning: null });
  normal.disconnect();
  assert.equal(normal.element('participant-overload-warning').hidden, true);
});
