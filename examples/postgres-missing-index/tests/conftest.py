import os
from collections.abc import Iterator

import pytest
from app.models import Base, Customer, IndexedOrder, Order
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

URL = os.environ.get(
    "QUERYGUARD_PG_URL", "postgresql+pg8000://postgres:pg@localhost:5432/postgres"
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 4))
        session.add_all(Order(id=i, customer_id=i % 3 + 1) for i in range(1, 7))
        session.add_all(IndexedOrder(id=i, customer_id=i % 3 + 1) for i in range(1, 7))
        session.commit()
        yield session
    engine.dispose()
