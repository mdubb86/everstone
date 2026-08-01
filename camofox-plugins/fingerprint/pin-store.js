import fs from 'node:fs';
import path from 'node:path';

// Load the persisted fingerprint pin, or generate + persist one on first use.
// The pin { fingerprint, seeds } is server-wide (Camoufox's fingerprint is per browser,
// not per context), so one file holds the stable device identity across restarts.
export function loadOrCreatePin(filePath, { makeFingerprint, makeSeeds }) {
  if (fs.existsSync(filePath)) return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  const pin = { fingerprint: makeFingerprint(), seeds: makeSeeds() };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(pin, null, 2));
  return pin;
}
