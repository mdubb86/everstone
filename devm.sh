#!/usr/bin/env bash
# devm.sh — provisioning function library for the everstone dev VM.
#
# Replaces the old devm.yaml `scripts:`/`install:`/`startup:` blocks (that shape is
# gone from the schema). Two function names are magic:
#   install()  — first boot only (marker-gated); one-time toolchain setup.
#   startup()  — every boot that opens the provisioning window; per-boot setup needing
#                unrestricted network.
# Both run as the guest `devm` user under passthrough egress (iron-proxy MITM'd but
# NOT allowlist-gated yet), with NOPASSWD sudo. A failure in either aborts provisioning
# (devm.target never starts, VM torn down) — so keep them idempotent and loud.

# ── Claude Code CLI (per `devm recipes get tool/ai/claude`) ──────────────────────────
# Two paths matter and this creates both:
#   /usr/local/bin/claude       the real binary, on the default system PATH (survives
#                               login-shell PATH resets).
#   ~devm/.local/bin/claude     the canonical path Claude self-checks for; it warns/
#                               refuses some operations without it.
# `sudo` on the privileged steps: a no-op when provisioning runs as root, required
# when it runs as `devm` (passwordless sudo) — works either way. `$HOME` instead of a
# hardcoded /root for the same reason. State (OAuth, transcripts, memory) is NOT here —
# it lives in the `claude` volume.
install-claude-cli() {
  # Symlink FIRST: the installer runs claude, which would otherwise write a fresh stub
  # at $HOME root that startup's save-back copies over the good volume config
  # (deterministic clobber, since install() precedes startup()).
  ln -sf /home/devm/.claude/.claude.json /home/devm/.claude.json
  curl -fsSL https://claude.ai/install.sh | bash
  sudo install -m 755 "$HOME/.local/share/claude/versions/"* /usr/local/bin/claude
  sudo install -d -o devm -g devm /home/devm/.local/bin
  sudo ln -sf /usr/local/bin/claude /home/devm/.local/bin/claude
}

# ── Node.js via fnm (per `devm recipes get tool/lang/node`) ──────────────────────────
# fnm installs as the devm user; the binary is copied to /usr/local/bin for system
# PATH, and the `path:` entry in devm.yaml puts node/npm/npx on PATH everywhere (incl.
# non-login SSH — what Orca's relay needs). --skip-shell: no ~/.bashrc hook.
install-fnm-and-node() {
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
  sudo install -m 755 /home/devm/.local/share/fnm/fnm /usr/local/bin/fnm
  fnm install --lts
  fnm default lts-latest
}

# ── uv (Python) (per `devm recipes get tool/lang/uv`) ────────────────────────────────
# CLAUDE.md mandates uv for es + e2e. Installed in the provisioning OPEN window, so
# astral.sh is reachable and NEVER needs a runtime allowlist entry (installing uv at
# runtime 403s — that's the trap). uv's rustls TLS through iron-proxy works via devm's
# auto-set UV_SYSTEM_CERTS=1.
install-uv() {
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
  env UV_INSTALL_DIR=/home/devm/.local/bin INSTALLER_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh
}

# ── Persist Claude's ~/.claude.json (login/account/project trust) ────────────────────
# It lives in $HOME, OUTSIDE the ~/.claude volume, so symlink it to a file ON the
# volume, re-linked every boot. NOT CLAUDE_CONFIG_DIR: the VS Code extension and Orca
# ignore that env and read/write the default ~/.claude.json regardless. Save-back
# guard: Claude writes config via atomic temp+rename, which replaces the symlink with a
# real file — fold it back into the volume before re-linking, but ONLY if it's
# genuinely onboarded (a mid-session clobber) or the volume has none yet (first seed).
# A fresh stub (no hasCompletedOnboarding) is discarded, never copied over good state.
link-claude-config() {
  if [ -f /home/devm/.claude.json ] && [ ! -L /home/devm/.claude.json ]; then
    if grep -qE '"hasCompletedOnboarding"[[:space:]]*:[[:space:]]*true' /home/devm/.claude.json 2>/dev/null \
       || [ ! -f /home/devm/.claude/.claude.json ]; then
      cp -f /home/devm/.claude.json /home/devm/.claude/.claude.json
    fi
  fi
  ln -sf /home/devm/.claude/.claude.json /home/devm/.claude.json
}

# ── Pre-seed the managed CPython in the startup OPEN window ───────────────────────────
# uv fetches python-build-standalone from release-assets.githubusercontent.com (a
# signed redirect that can't be allowlisted), so downloading it here keeps that host
# OUT of the runtime allowlist. Idempotent (fast no-op once cached VM-local). Reads
# .python-version (3.12). $WORKSPACE = the primary repo's guest path.
preseed-python() {
  /home/devm/.local/bin/uv python install \
    "$(cat "$WORKSPACE/.python-version" 2>/dev/null || echo 3.12)"
}

# ── Magic entrypoints ────────────────────────────────────────────────────────────────
install() {
  install-claude-cli
  install-fnm-and-node
  install-uv
}

startup() {
  link-claude-config
  preseed-python
}
