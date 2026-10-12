import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


def test_orders_with_test_client(client):
    response = client.get("/orders")
    assert response.status_code == 200
    assert len(response.json()) == 5


@pytest.mark.anyio
async def test_orders_with_async_client(async_app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/async-orders")
    assert response.status_code == 200
    assert len(response.json()) == 5
