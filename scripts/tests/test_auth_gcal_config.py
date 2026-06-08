import os, importlib.util, pathlib


def _load_mod():
    p = pathlib.Path(__file__).parents[1] / "auth_gcal.py"
    spec = importlib.util.spec_from_file_location("auth_gcal", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_gcal_config_reads_from_config_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "public_url: https://es.example.com/\n"
        "gcalcli: {client_id: CID, client_secret: CSEC}\n"
    )
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg))
    import pytest
    try:
        import es.config  # noqa
    except Exception:
        pytest.skip("es package not importable in this test env")
    m = _load_mod()
    cid, csec, url = m._gcal_config()
    assert cid == "CID"
    assert csec == "CSEC"
    assert url == "https://es.example.com"   # trailing slash stripped
