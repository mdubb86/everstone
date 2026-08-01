import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readCamouConfig, writeCamouConfig, overlayPinnedFingerprint } from './config-overlay.js';

// Camoufox reads its fingerprint config from CAMOU_CONFIG_1, _2, ... env vars:
// JSON.stringify(configMap) split into chunkSize-length string slices. These helpers
// must decode/re-encode that exact scheme so the pinning overlay survives to the binary.

test('writeCamouConfig then readCamouConfig round-trips a config map', () => {
  const env = {};
  const map = { 'navigator.userAgent': 'UA-X', 'canvas:seed': 42, nested: { a: 1 } };
  writeCamouConfig(env, map);
  assert.deepEqual(readCamouConfig(env), map);
});

test('encoding matches camoufox-js: CAMOU_CONFIG_1 is JSON.stringify(map) for a small config', () => {
  const env = {};
  const map = { a: 1 };
  writeCamouConfig(env, map);
  assert.equal(env.CAMOU_CONFIG_1, JSON.stringify(map));
  assert.equal(env.CAMOU_CONFIG_2, undefined);
});

test('multi-chunk: long JSON is split across CAMOU_CONFIG_n and reassembled', () => {
  const env = {};
  const map = { big: 'x'.repeat(50) };
  writeCamouConfig(env, map, 10); // tiny chunk size forces splitting
  const chunks = Math.ceil(JSON.stringify(map).length / 10);
  assert.ok(chunks > 1, 'test must exercise more than one chunk');
  for (let i = 1; i <= chunks; i++) assert.equal(typeof env[`CAMOU_CONFIG_${i}`], 'string');
  assert.deepEqual(readCamouConfig(env), map);
});

test('readCamouConfig returns null when no CAMOU_CONFIG_* present', () => {
  assert.equal(readCamouConfig({ PATH: '/bin' }), null);
});

test('overlay replaces matching keys, adds new ones, preserves the rest', () => {
  const env = {};
  writeCamouConfig(env, { 'navigator.userAgent': 'OLD', keep: true });
  overlayPinnedFingerprint(env, { 'navigator.userAgent': 'NEW', 'canvas:seed': 7 });
  assert.deepEqual(readCamouConfig(env), { 'navigator.userAgent': 'NEW', keep: true, 'canvas:seed': 7 });
});

test('re-encoding a shorter config deletes stale higher-numbered chunks', () => {
  const env = {};
  writeCamouConfig(env, { big: 'x'.repeat(50) }, 10); // many chunks
  assert.notEqual(env.CAMOU_CONFIG_3, undefined);
  writeCamouConfig(env, { a: 1 }, 10); // now a single chunk
  assert.equal(env.CAMOU_CONFIG_2, undefined);
  assert.equal(env.CAMOU_CONFIG_3, undefined);
  assert.deepEqual(readCamouConfig(env), { a: 1 });
});
