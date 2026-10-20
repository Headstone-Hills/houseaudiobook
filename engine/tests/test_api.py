from fastapi.testclient import TestClient

from hillaudio import __version__
from hillaudio.api.app import create_app


def test_health() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}
