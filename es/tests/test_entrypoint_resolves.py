def test_mcp_http_main_importable():
    from es.mcp_http import main
    assert callable(main)


def test_mcp_http_deps_importable():
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401
