def test_health_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_capabilities_requires_auth(client):
    resp = client.get("/v1/capabilities")
    assert resp.status_code == 401


def test_capabilities_with_auth(client):
    resp = client.get("/v1/capabilities", headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert "capabilities" in body
    names = {c["name"] for c in body["capabilities"]}
    assert "web_search" in names
    assert "scrape" in names
