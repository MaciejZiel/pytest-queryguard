from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app, get_async_session, get_session
from app.models import Base, Customer, Order
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def seed(session: Session) -> None:
    session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 6))
    session.add_all(Order(id=i, customer_id=i) for i in range(1, 6))
    session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The sync dependency replaced by a session on an in-memory database."""
    # sync endpoints run in a worker thread, so SQLite must allow other threads
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as session:
        seed(session)

    def override() -> Iterator[Session]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
async def async_app() -> AsyncIterator[None]:
    """The async dependency replaced by a session on an in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 6))
        session.add_all(Order(id=i, customer_id=i) for i in range(1, 6))
        await session.commit()

    async def override() -> AsyncIterator[object]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    yield
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def anyio_backend() -> str:
    # aiosqlite needs asyncio; without this anyio also tries trio when installed
    return "asyncio"
