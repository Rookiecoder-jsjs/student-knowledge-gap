def test_router_mode_uses_internal_token(monkeypatch):
    from app.llm.audit import unwrap
    from app.llm.client import get_client, set_client

    set_client(None)
    monkeypatch.setenv("SC_LLM_PROVIDER", "mock")
    monkeypatch.setenv("SC_LLM_ROUTER_URL", "http://router:8090/v1")
    monkeypatch.setenv("SC_LLM_ROUTER_TOKEN", "router-secret")
    monkeypatch.setenv("SC_LLM_API_KEY", "provider-secret")
    client = unwrap(get_client("vision"))
    assert client.base_url == "http://router:8090/v1"
    assert client.api_key == "router-secret"
    assert client.capability == "vision"
    set_client(None)
