"""Regression: a fresh EverStone boot must ship CLEAN — the everstone profile is
created with `--no-skills` (so the `.no-bundled-skills` opt-out marker is present)
and Hermes's full stock skill bundle (dogfood/yuanbao/apple/creative/…) is NOT
seeded. Bug: configure.py used to mkdir profiles/<name>/ (to render SOUL.md) at
the entrypoint, BEFORE setup_hermes ran the canonical `--no-skills` create — the
create lost the race, the marker was never written, and the gateway bundled the
full set. Fix: configure.py no longer touches the profile dir; render_soul.py
renders SOUL.md from setup_hermes AFTER the create. The e2e fixture boots a fresh
(empty) data dir, so this exercises that exact create path.
"""
import subprocess


def _exec(container, *args):
    return subprocess.run(["docker", "exec", container, *args],
                          capture_output=True, text=True)


def test_profile_created_no_bundled_skills(everstone):
    c = everstone["container_name"]
    r = _exec(c, "test", "-f", "/opt/data/hermes/profiles/everstone/.no-bundled-skills")
    assert r.returncode == 0, (
        "missing /opt/data/hermes/profiles/everstone/.no-bundled-skills — the "
        "canonical `hermes profile create --no-skills` lost the race (something "
        "pre-created the profile dir before it ran)."
    )


def test_no_stock_skill_bundle(everstone):
    c = everstone["container_name"]
    skills = _exec(
        c, "sh", "-c", "ls /opt/data/hermes/profiles/everstone/skills/ 2>/dev/null"
    ).stdout.split()
    for stock in ("dogfood", "yuanbao", "apple", "creative"):
        assert stock not in skills, (
            f"stock skill '{stock}' was bundled into a fresh profile — ship-clean "
            f"regression (skills present: {skills})"
        )


def test_soul_rendered_into_profile(everstone):
    # render_soul.py runs after the create → the profile's SOUL.md is rendered
    # from config.agent.soul (here "You are a test assistant.") on every boot.
    c = everstone["container_name"]
    r = _exec(c, "cat", "/opt/data/hermes/profiles/everstone/SOUL.md")
    assert r.returncode == 0, "profile SOUL.md missing — render_soul.py didn't run"
    assert "test assistant" in r.stdout.lower(), (
        f"SOUL.md not rendered from config.agent.soul (got: {r.stdout[:200]!r})"
    )
