import subprocess, os, stat, textwrap, pathlib

LAUNCHER = pathlib.Path(__file__).resolve().parents[1] / "es-mcp-http-run"

def test_launcher_idles_when_disabled(tmp_path, monkeypatch):
    # Fake `config-get` returning empty (disabled) and a fake `exec tail` sentinel.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "config-get").write_text("#!/bin/sh\necho ''\n")
    (bin_dir / "es-mcp-http").write_text("#!/bin/sh\necho STARTED_SERVER\n")
    for f in ("config-get", "es-mcp-http"):
        os.chmod(bin_dir / f, 0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    # Run with a tiny timeout: when disabled it execs `tail -f /dev/null` (blocks) → timeout.
    try:
        out = subprocess.run(["sh", str(LAUNCHER)], env=env, capture_output=True,
                             text=True, timeout=2)
        printed = out.stdout
    except subprocess.TimeoutExpired as e:
        printed = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    assert "disabled" in printed
    assert "STARTED_SERVER" not in printed
