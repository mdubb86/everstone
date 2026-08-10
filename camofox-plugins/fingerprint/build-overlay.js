// Turn a persisted pin into the CAMOU_CONFIG overlay: the BrowserForge fingerprint keys
// (navigator/screen/webgl/… via fromBrowserforge) plus the three fixed noise seeds that
// make canvas/audio/fonts deterministic (proven necessary in the M0a spike, D16a).
export function buildOverlay(pin, { fromBrowserforge, ffVersion }) {
  return {
    ...fromBrowserforge(pin.fingerprint, ffVersion),
    'canvas:seed': pin.seeds.canvas,
    'audio:seed': pin.seeds.audio,
    'fonts:spacing_seed': pin.seeds.fonts,
  };
}
