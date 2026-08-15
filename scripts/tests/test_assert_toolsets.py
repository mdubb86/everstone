"""Tool-surface assertion tests.

The property under test is EverStone's core security boundary: the agent is
locked to a curated tool set and reaches capabilities only through es. Before
this script the boundary had two holes — the allowlist was seeded once at
profile creation (so drift was never corrected) and it never covered cron.
"""
import pathlib

import yaml

import assert_toolsets as at


def _write(tmp_path: pathlib.Path, cfg: dict) -> pathlib.Path:
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


def _read(tmp_path: pathlib.Path) -> dict:
    return yaml.safe_load((tmp_path / "config.yaml").read_text())


def test_cron_is_narrower_than_the_dm_surface():
    """A cron agent runs unattended, so it gets LESS than the DM surface — not
    the full default toolset it used to inherit by being unlisted."""
    assert at.CRON_TOOLSETS == ["skills"]
    assert set(at.CRON_TOOLSETS) < set(at.TELEGRAM_TOOLSETS)


def test_no_shell_or_filesystem_toolset_is_ever_granted():
    """The invariant CLAUDE.md states: no terminal, no file, no code execution.
    Asserted for BOTH surfaces, because cron previously had all three."""
    forbidden = {"terminal", "file", "coding", "code_execution", "debugging",
                 "delegation", "process"}
    assert not (set(at.TELEGRAM_TOOLSETS) & forbidden)
    assert not (set(at.CRON_TOOLSETS) & forbidden)


def test_cron_has_no_browser():
    """Interactive browsing is the classic prompt-injection vector and there is
    nobody watching at 3am. es_web_fetch covers cheap page reads, and es_maps_*
    drives camofox-AUTH server-side inside es — never through browser_*."""
    assert "browser" not in at.CRON_TOOLSETS


def test_denylist_covers_the_shell_and_file_toolsets():
    for name in ("terminal", "coding", "file", "code_execution"):
        assert name in at.DISABLED_TOOLSETS


def test_applies_the_surface_to_an_empty_profile(tmp_path):
    _write(tmp_path, {})
    at.main(tmp_path)
    got = _read(tmp_path)
    assert got["platform_toolsets"]["cron"] == at.CRON_TOOLSETS
    assert got["platform_toolsets"]["telegram"] == at.TELEGRAM_TOOLSETS
    assert got["agent"]["disabled_toolsets"] == at.DISABLED_TOOLSETS


def test_corrects_a_widened_surface(tmp_path):
    """THE regression: a drifted config must be put back. Seeding once at
    profile-create could not do this, which is why a widened allowlist survived
    indefinitely."""
    _write(tmp_path, {"platform_toolsets": {"telegram": ["terminal", "file", "browser"],
                                            "cron": ["terminal"]}})
    at.main(tmp_path)
    got = _read(tmp_path)
    assert got["platform_toolsets"]["telegram"] == at.TELEGRAM_TOOLSETS
    assert got["platform_toolsets"]["cron"] == at.CRON_TOOLSETS
    assert "terminal" not in str(got["platform_toolsets"])


def test_adds_cron_to_a_profile_that_predates_it(tmp_path):
    """Existing installs have telegram but no cron key — that absence is what
    granted cron the full toolset."""
    _write(tmp_path, {"platform_toolsets": {"telegram": at.TELEGRAM_TOOLSETS}})
    at.main(tmp_path)
    assert _read(tmp_path)["platform_toolsets"]["cron"] == at.CRON_TOOLSETS


def test_leaves_operator_owned_keys_alone(tmp_path):
    """Only the keys we assert are ours; model, provider, compression and the
    rest are operator-owned per the Hermes config contract."""
    _write(tmp_path, {"model": "openai-codex/gpt-5.5",
                      "compression": {"threshold": 0.85},
                      "agent": {"soul_extra": "keep me"}})
    at.main(tmp_path)
    got = _read(tmp_path)
    assert got["model"] == "openai-codex/gpt-5.5"
    assert got["compression"] == {"threshold": 0.85}
    assert got["agent"]["soul_extra"] == "keep me"
    assert got["agent"]["disabled_toolsets"] == at.DISABLED_TOOLSETS


def test_is_idempotent_and_reports_no_change(tmp_path, capsys):
    _write(tmp_path, {})
    at.main(tmp_path)
    capsys.readouterr()
    at.main(tmp_path)
    assert "matches policy" in capsys.readouterr().out


def test_missing_profile_is_not_fatal(tmp_path):
    """setup_hermes seeds the profile before calling us; a missing config means
    profile creation already failed, and that error is the louder one."""
    assert at.main(tmp_path / "nope") == 0
