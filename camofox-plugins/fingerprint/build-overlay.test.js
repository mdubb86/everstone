import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildOverlay } from './build-overlay.js';

test('overlay merges fromBrowserforge output with the three fixed seed keys', () => {
  const pin = { fingerprint: { marker: 'FP' }, seeds: { canvas: 111, audio: 222, fonts: 333 } };
  const overlay = buildOverlay(pin, {
    fromBrowserforge: (fp, ver) => ({ 'navigator.userAgent': `UA/${ver}`, _fp: fp.marker }),
    ffVersion: '152',
  });
  assert.deepEqual(overlay, {
    'navigator.userAgent': 'UA/152',
    _fp: 'FP',
    'canvas:seed': 111,
    'audio:seed': 222,
    'fonts:spacing_seed': 333,
  });
});
