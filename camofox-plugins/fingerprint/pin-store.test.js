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

test('a CORRUPT pin regenerates instead of throwing (never launch unpinned)', () => {
  const file = tmpFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{ this is not json');
  let corruptErr = null;
  const pin = loadOrCreatePin(file, {
    makeFingerprint: () => ({ ua: 'regenerated' }),
    makeSeeds: () => ({ canvas: 4, audio: 5, fonts: 6 }),
    onCorrupt: (e) => { corruptErr = e; },
  });
  assert.ok(corruptErr, 'caller must be told the pin was corrupt');
  assert.deepEqual(pin.fingerprint, { ua: 'regenerated' });
  // and the healed pin must be PERSISTED, or every launch regenerates = the drift we avoid
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), pin);
});

test('a structurally invalid pin (parses, but no fingerprint/seeds) also regenerates', () => {
  const file = tmpFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{"unexpected": true}');
  let told = false;
  const pin = loadOrCreatePin(file, {
    makeFingerprint: () => ({ ua: 'fresh' }),
    makeSeeds: () => ({ canvas: 1, audio: 2, fonts: 3 }),
    onCorrupt: () => { told = true; },
  });
  assert.ok(told);
  assert.deepEqual(pin, { fingerprint: { ua: 'fresh' }, seeds: { canvas: 1, audio: 2, fonts: 3 } });
});

test('an unwritable pin location still THROWS (not recoverable — caller must fail closed)', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pinstore-ro-'));
  fs.chmodSync(dir, 0o500);  // r-x: cannot create the pin file
  try {
    assert.throws(() => loadOrCreatePin(path.join(dir, 'fingerprint.json'), {
      makeFingerprint: () => ({ ua: 'X' }),
      makeSeeds: () => ({ canvas: 1, audio: 2, fonts: 3 }),
    }));
  } finally {
    fs.chmodSync(dir, 0o700);
  }
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
