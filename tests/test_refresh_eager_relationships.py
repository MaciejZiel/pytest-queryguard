"""``session.refresh()`` of an object whose relationship is mapped eager.

Refreshing a collection mapped ``lazy="selectin"`` (or reaching one through a
``lazy="joined"`` relationship) runs the mapped eager loader for that one
object. The loader's query is not a lazy load, so it is not an N+1 and the
hint to use ``selectinload`` would name the strategy that is already mapped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    immediateload,
    mapped_column,
    relationship,
)
from sqlalchemy.pool import StaticPool

from pytest_queryguard import NPlusOneDetected, record_queries

# pytester-free, but the outer plugin watches these tests too.
pytestmark = pytest.mark.allow_nplusone


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "author"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    books: Mapped[list[Book]] = relationship(back_populates="author", lazy="selectin")
    prizes: Mapped[list[Prize]] = relationship(lazy="subquery")
    awards: Mapped[list[Award]] = relationship(lazy="joined")
    # the strategy the finding is about: loaded one parent at a time
    notes: Mapped[list[Note]] = relationship(lazy="select")


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped[Author] = relationship(back_populates="books")


class Prize(Base):
    __tablename__ = "prize"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))


class Award(Base):
    __tablename__ = "award"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))


class Note(Base):
    __tablename__ = "note"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))


class Review(Base):
    """A row whose ``author`` is mapped joined, like the issue's token -> user."""

    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped[Author] = relationship(lazy="joined")


ROWS = 6


def _seed(session: Session) -> None:
    for i in range(1, ROWS + 1):
        session.add(
            Author(
                id=i,
                name=f"a{i}",
                books=[Book(id=i)],
                prizes=[Prize(id=i)],
                awards=[Award(id=i)],
                notes=[Note(id=i)],
            )
        )
    session.commit()


@pytest.fixture
def engine() -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        _seed(seed)
    yield engine
    engine.dispose()


def _report(log: Any) -> str:
    try:
        log.assert_no_nplusone()
    except NPlusOneDetected as exc:
        return str(exc)
    return ""


def _create(session: Session, id_: int) -> Author:
    # the repository "create": add, flush, refresh the one new row
    author = Author(id=id_, name=f"n{id_}")
    session.add(author)
    session.flush()
    session.refresh(author)
    return author


def test_two_creates_with_refresh_are_clean(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        _create(session, 100)
        _create(session, 101)
    assert _report(log) == ""


def test_many_creates_with_refresh_report_no_relationship_load(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in range(100, 108):
            _create(session, i)
    assert "lazy-loaded" not in _report(log)
    assert "selectinload" not in _report(log)


def test_refresh_with_attribute_names_is_clean(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        a, b = session.get(Author, 1), session.get(Author, 2)
        assert a is not None and b is not None
        session.refresh(a, ["books"])
        session.refresh(b, ["books"])
    assert _report(log) == ""


@pytest.mark.parametrize("attr", ["books", "prizes", "awards"])
def test_each_eager_strategy_is_clean_with_named_refresh(
    engine: Any, attr: str
) -> None:
    with Session(engine) as session, record_queries() as log:
        authors = [session.get(Author, i) for i in (1, 2, 3)]
        for author in authors:
            assert author is not None
            session.refresh(author, [attr])
    assert "lazy-loaded" not in _report(log)


def test_joined_relationship_pulling_a_selectin_collection_is_clean(
    engine: Any,
) -> None:
    with Session(engine) as session:
        session.add_all([Review(id=1, author_id=1), Review(id=2, author_id=2)])
        session.commit()
        session.expunge_all()
    with Session(engine) as session, record_queries() as log:
        for review_id in (1, 2):
            review = session.get(Review, review_id)
            assert review is not None
            session.refresh(review)
    assert "lazy-loaded" not in _report(log)


def test_loading_one_by_one_is_clean(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in (1, 2, 3):
            session.get(Author, i)
        for i in (4, 5):
            session.scalars(select(Author).where(Author.id == i)).unique().one()
    assert "lazy-loaded" not in _report(log)


def test_a_real_lazy_load_next_to_refreshes_is_still_reported(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        authors = session.scalars(select(Author)).unique().all()
        _create(session, 100)
        _create(session, 101)
        for author in authors:
            _ = author.notes  # lazy="select": one query per author
    report = _report(log)
    assert f"Author.notes was lazy-loaded {ROWS} times" in report
    assert "Author.books" not in report
    assert "Author.prizes" not in report


def test_a_refresh_loop_followed_by_lazy_access_is_reported(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for author in session.scalars(select(Author)).unique().all():
            session.refresh(author)
            _ = author.notes
    report = _report(log)
    assert f"Author.notes was lazy-loaded {ROWS} times" in report
    assert "Author.books" not in report


def test_a_refresh_loop_is_still_a_column_load_finding(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for author in session.scalars(select(Author)).unique().all():
            session.refresh(author)
    report = _report(log)
    assert f"reloaded with session.refresh() {ROWS} times" in report
    assert "lazy-loaded" not in report


def test_an_immediateload_query_is_still_a_lazy_load_per_parent(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        session.scalars(
            select(Author).options(immediateload(Author.notes))
        ).unique().all()
    assert f"Author.notes was lazy-loaded {ROWS} times" in _report(log)


def test_expired_selectin_collections_are_an_expired_column_load(engine: Any) -> None:
    # SQLAlchemy reloads the expired object through the same refresh path, so
    # the mapped selectin load is not a lazy load; the loop is the expired
    # column-load finding
    with Session(engine) as session, record_queries() as log:
        authors = session.scalars(select(Author)).unique().all()
        session.expire_all()
        for author in authors:
            _ = author.books
    report = _report(log)
    assert "Author.books was lazy-loaded" not in report
    assert "Author (expired)" in report


# ------------------------------------------------------------------- async


def _run(work: Any) -> Any:
    async def main() -> Any:
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as seed:
                for i in range(1, ROWS + 1):
                    seed.add(
                        Author(
                            id=i,
                            name=f"a{i}",
                            books=[Book(id=i)],
                            prizes=[Prize(id=i)],
                            awards=[Award(id=i)],
                            notes=[Note(id=i)],
                        )
                    )
                await seed.commit()
            with record_queries() as log:
                async with maker() as session:
                    await work(session)
            return log
        finally:
            await engine.dispose()

    return asyncio.run(main())


def test_async_two_creates_with_refresh_are_clean() -> None:
    async def work(session: AsyncSession) -> None:
        for i in (100, 101):
            author = Author(id=i, name=f"n{i}")
            session.add(author)
            await session.flush()
            await session.refresh(author)

    assert _report(_run(work)) == ""


def test_async_refresh_with_attribute_names_is_clean() -> None:
    async def work(session: AsyncSession) -> None:
        for i in (1, 2, 3):
            author = await session.get(Author, i)
            assert author is not None
            await session.refresh(author, ["books"])

    assert _report(_run(work)) == ""


def test_async_refresh_next_to_a_real_lazy_load_is_still_reported() -> None:
    async def work(session: AsyncSession) -> None:
        authors = (await session.scalars(select(Author))).unique().all()
        for i in (100, 101):
            author = Author(id=i, name=f"n{i}")
            session.add(author)
            await session.flush()
            await session.refresh(author)
        for author in authors:
            await author.awaitable_attrs.notes

    report = _report(_run(work))
    assert f"Author.notes was lazy-loaded {ROWS} times" in report
    assert "Author.books" not in report
