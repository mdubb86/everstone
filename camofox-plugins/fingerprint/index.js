/**
 * EverStone fingerprint-pinning plugin for camofox-browser.
 *
 * camofox-browser generates a FRESH random Camoufox fingerprint on every browser launch
 * (great for anonymous scraping, fatal for a durable logged-in session — the drifting
 * canvas/fingerprint reads as a new device and forces Google re-verification). This plugin
 * pins ONE stable identity: it persists a fingerprint + fixed canvas/audio/fonts noise seeds
 * on first use, then injects them into the launch config on every `browser:launching`.
 *
 * Loaded as a plugin (added under plugins/, enabled in camofox.config.json) — the upstream
 * server source is never patched. See docs/superpowers/specs/2026-07-31-everstone-maps-design.md
 * (D16, D16a). The mechanism was proven empirically before this was written.
 */
import path from 'node:path';
import crypto from 'node:crypto';
import { generateFingerprint, fromBrowserforge } from 'camoufox-js/dist/fingerprints.js';
import { installedVerStr } from 'camoufox-js/dist/pkgman.js';
import { loadOrCreatePin } from './pin-store.js';
import { buildOverlay } from './build-overlay.js';
import { overlayPinnedFingerprint } from './config-overlay.js';

export async function register(app, ctx, pluginConfig = {}) {
  const { events, config, log } = ctx;

  const profileDir = process.env.CAMOFOX_PROFILE_DIR || pluginConfig.profileDir || config?.profileDir;
  const pinPath = pluginConfig.pinPath ||
    path.join(profileDir ? path.dirname(profileDir) : '/opt/data/browser', 'fingerprint.json');
  const os = pluginConfig.os || 'linux';
  const window = pluginConfig.window || [1280, 720];

  // Compute the pinned overlay once at startup. The fingerprint is per-browser (server-wide),
  // so a single persisted pin holds the device identity across restarts.
  let overlay;
  try {
    const ffVersion = installedVerStr().split('.')[0];
    const pin = loadOrCreatePin(pinPath, {
      makeFingerprint: () => generateFingerprint(window, { operatingSystems: [os] }),
      makeSeeds: () => ({
        canvas: crypto.randomInt(1, 2 ** 31),
        audio: crypto.randomInt(1, 2 ** 31),
        fonts: crypto.randomInt(1, 2 ** 31),
      }),
    });
    overlay = buildOverlay(pin, { fromBrowserforge, ffVersion });
    log('info', 'fingerprint plugin: pinned identity loaded', { pinPath, os, ffVersion });
  } catch (err) {
    // Fail-open: a pinning failure must not stop the browser from launching (it just runs
    // unpinned, and the health probe / durability watch will surface it).
    log('error', 'fingerprint plugin: could not load/generate pin — launching UNPINNED', { error: err.message });
    return;
  }

  events.on('browser:launching', ({ options }) => {
    try {
      if (!options.env) options.env = {};
      overlayPinnedFingerprint(options.env, overlay);
    } catch (err) {
      log('warn', 'fingerprint plugin: overlay failed this launch (unpinned)', { error: err.message });
    }
  });

  log('info', 'fingerprint plugin enabled', { pinPath });
}
