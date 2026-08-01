import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadOrCreatePin } from './pin-store.js';

function tmpFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pinstore-'));
  return path.join(dir, 'nested', 'fingerprint.json'); // nested → exercises mkdir
}

test('first call generates a pin, persists it, and returns it', () => {
  const file = tmpFile();
  let fpCalls = 0, seedCalls = 0;
  const pin = loadOrCreatePin(file, {
    makeFingerprint: () => { fpCalls++; return { ua: 'X' }; },
    makeSeeds: () => { seedCalls++; return { canvas: 1, audio: 2, fonts: 3 }; },
  });
  assert.deepEqual(pin, { fingerprint: { ua: 'X' }, seeds: { canvas: 1, audio: 2, fonts: 3 } });
  assert.equal(fpCalls, 1);
  assert.equal(seedCalls, 1);
  assert.ok(fs.existsSync(file), 'pin file should be written');
});

test('second call returns the persisted pin WITHOUT regenerating', () => {
  const file = tmpFile();
  const deps = {
    makeFingerprint: () => ({ ua: 'first', r: 1 }),
    makeSeeds: () => ({ canvas: 9, audio: 9, fonts: 9 }),
  };
  const first = loadOrCreatePin(file, deps);
  let regenerated = false;
  const second = loadOrCreatePin(file, {
    makeFingerprint: () => { regenerated = true; return { ua: 'SHOULD-NOT-HAPPEN' }; },
    makeSeeds: () => { regenerated = true; return {}; },
  });
  assert.equal(regenerated, false, 'must not regenerate when a pin already exists');
  assert.deepEqual(second, first);
});
