// Test voor de multimodale omzetting die WhatsApp-afbeeldingen (manager-
// kanaal) naar het juiste providerformaat brengt. Puur functioneel — geen
// Neon-verbinding of echte LLM-key nodig, zie CLAUDE.md-les onder 14a/14b:
// twee providers, één omzetting, en die moet voor beide kloppen.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toMultimodalUserMessage } from '../api/_iris_core.js';

const IMAGE = { mediaType: 'image/jpeg', base64: 'ZmFrZS1ieXRlcw==' };

test('openrouter: tekst + image_url, in die volgorde', () => {
  const msg = toMultimodalUserMessage('Zet dit in mijn agenda', IMAGE, 'openrouter');
  assert.equal(msg.role, 'user');
  assert.equal(msg.content.length, 2);
  assert.deepEqual(msg.content[0], { type: 'text', text: 'Zet dit in mijn agenda' });
  assert.equal(msg.content[1].type, 'image_url');
  assert.equal(msg.content[1].image_url.url, 'data:image/jpeg;base64,ZmFrZS1ieXRlcw==');
});

test('openrouter: zonder tekst alleen het image-blok', () => {
  const msg = toMultimodalUserMessage('', IMAGE, 'openrouter');
  assert.equal(msg.content.length, 1);
  assert.equal(msg.content[0].type, 'image_url');
});

test('openmodel/anthropic: image-blok eerst, dan tekst', () => {
  const msg = toMultimodalUserMessage('Zet dit in mijn agenda', IMAGE, 'openmodel');
  assert.equal(msg.content.length, 2);
  assert.deepEqual(msg.content[0], {
    type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: 'ZmFrZS1ieXRlcw==' },
  });
  assert.deepEqual(msg.content[1], { type: 'text', text: 'Zet dit in mijn agenda' });
});

test('onbekende provider valt terug op het anthropic-formaat', () => {
  const msg = toMultimodalUserMessage('', IMAGE, 'iets-anders');
  assert.equal(msg.content[0].type, 'image');
});
