import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const script = readFileSync(new URL('../../../snake_experiment_ui/static/participant_language.js', import.meta.url), 'utf8');

function client(saved, blocked = false) {
  const values = new Map(saved ? [['participant_language', saved]] : []);
  const select = new EventTarget();
  const node = { nodeValue: 'Waiting for the experimenter' };
  const document = {
    body: {}, documentElement: {}, title: '',
    createTreeWalker() { let next = true; return { currentNode: node, nextNode() { if (!next) return false; next = false; return true; } }; },
    querySelectorAll() { return []; }, getElementById() { return select; },
  };
  const window = new EventTarget();
  const localStorage = { getItem(key) { if (blocked) throw Error(); return values.get(key); }, setItem(key, value) { if (blocked) throw Error(); values.set(key, value); } };
  vm.runInNewContext(script, { document, window, localStorage, NodeFilter: { SHOW_TEXT: 4 }, CustomEvent: class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } } });
  return { select, node, document, window, values };
}

test('configured default, switching, and saved preference', () => {
  const c = client();
  c.window.participantI18n.initialize('french');
  assert.equal(c.document.documentElement.lang, 'fr');
  assert.equal(c.node.nodeValue, 'En attente de l’expérimentateur');
  assert.equal(c.window.participantI18n.t('Go to target 2'), 'Aller à la cible 2');
  c.select.value = 'english'; c.select.dispatchEvent(new Event('change'));
  assert.equal(c.node.nodeValue, 'Waiting for the experimenter');
  assert.equal(c.values.get('participant_language'), 'english');
  const restored = client('english');
  restored.window.participantI18n.initialize('french');
  assert.equal(restored.document.documentElement.lang, 'en');
});

test('selection works when browser storage is unavailable', () => {
  const c = client(null, true);
  c.window.participantI18n.initialize('english');
  c.select.value = 'french'; c.select.dispatchEvent(new Event('change'));
  assert.equal(c.document.documentElement.lang, 'fr');
});
