def test_mcp_http_main_importable():
    from es.mcp_http import main
    assert callable(main)


def test_oauth_deps_importable():
    import authlib  # noqa: F401
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401
