from app.main import app
from fastapi.testclient import TestClient


def test_orders_endpoint():
    with TestClient(app) as client:
        response = client.get("/orders")
    assert response.status_code == 200
    assert len(response.json()) == 5
