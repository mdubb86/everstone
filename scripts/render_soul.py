#!/usr/bin/env python3
"""Render config.agent.soul into the active Hermes profile's SOUL.md.

Run by setup_hermes AFTER `hermes profile create --no-skills`, so the profile is
created clean (with the bundled-skill opt-out marker) BEFORE anything writes into
it. This is deliberately NOT done in configure.py: configure.py runs first (at the
entrypoint) and a pre-emptive mkdir of profiles/<name>/ would win the race against
the canonical --no-skills create, leaving the profile bundled.

Idempotent: always overwrites SOUL.md from config.yaml (the source of truth for the
persona). Safe to run on every boot.
"""
import configure

if __name__ == "__main__":
    configure.generate_hermes_soul(configure.load_config())
