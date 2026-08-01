// Camoufox reads its fingerprint config from CAMOU_CONFIG_1, _2, ... env vars: the string
// JSON.stringify(configMap) split into chunkSize-length slices (see camoufox-js getEnvVars()).
// These helpers decode/re-encode that exact scheme so a pinned-fingerprint overlay reaches
// the browser binary intact. chunkSize defaults to Camoufox's non-Windows value (32767).

const KEY = 'CAMOU_CONFIG_';

export function readCamouConfig(env) {
  let str = '';
  for (let i = 1; env[`${KEY}${i}`] !== undefined; i++) str += env[`${KEY}${i}`];
  return str === '' ? null : JSON.parse(str);
}

export function writeCamouConfig(env, configMap, chunkSize = 32767) {
  // Clear existing chunks first so a shorter config leaves no stale tail (CAMOU_CONFIG_3…).
  for (let i = 1; env[`${KEY}${i}`] !== undefined; i++) delete env[`${KEY}${i}`];
  const str = JSON.stringify(configMap);
  for (let i = 0, n = 1; i < str.length; i += chunkSize, n++) {
    env[`${KEY}${n}`] = str.slice(i, i + chunkSize);
  }
  return env;
}

export function overlayPinnedFingerprint(env, overlay, chunkSize = 32767) {
  const configMap = readCamouConfig(env) || {};
  Object.assign(configMap, overlay);
  return writeCamouConfig(env, configMap, chunkSize);
}
