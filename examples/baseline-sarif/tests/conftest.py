from collections.abc import Iterator

import pytest
from app.models import Author, Base, Book
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Author(id=i, name=f"author {i}") for i in range(1, 9))
        session.add_all(Book(id=i, title=f"book {i}", author_id=i) for i in range(1, 9))
        session.commit()
        session.expunge_all()
        yield session
