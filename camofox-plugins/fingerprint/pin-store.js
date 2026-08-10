import fs from 'node:fs';
import path from 'node:path';

// Load the persisted fingerprint pin, or generate + persist one on first use.
// The pin { fingerprint, seeds } is server-wide (Camoufox's fingerprint is per browser,
// not per context), so one file holds the stable device identity across restarts.
//
// A MISSING pin is the normal first-run path, not a failure — generate and persist it.
//
// A CORRUPT pin SELF-HEALS. Previously JSON.parse threw and, because the caller fails open,
// the browser launched UNPINNED — a brand-new random device on EVERY launch, indefinitely,
// until a human deleted the file. That is exactly the drift this plugin exists to prevent,
// and it announced itself as a single log line. Regenerating costs at most one re-verification
// and is stable thereafter. `onCorrupt` is how the caller gets to say so loudly. Failures that
// are NOT recoverable (unwritable dir, generator throwing) still propagate to the caller.
export function loadOrCreatePin(filePath, { makeFingerprint, makeSeeds, onCorrupt }) {
  if (fs.existsSync(filePath)) {
    try {
      const pin = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      if (pin && pin.fingerprint && pin.seeds) return pin;
      throw new Error('pin file parsed but has no fingerprint/seeds');
    } catch (err) {
      if (onCorrupt) onCorrupt(err);
    }
  }
  const pin = { fingerprint: makeFingerprint(), seeds: makeSeeds() };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(pin, null, 2));
  return pin;
}
