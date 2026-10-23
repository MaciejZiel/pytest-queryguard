from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    defer,
    deferred,
    joinedload,
    load_only,
    mapped_column,
    relationship,
    selectinload,
    undefer,
)
from sqlalchemy.pool import StaticPool

from pytest_queryguard import NPlusOneDetected, record_queries
from pytest_queryguard.detectors import detect
from pytest_queryguard.messages import make_findings

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

ROWS = 6


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "author"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    books: Mapped[list[Book]] = relationship(back_populates="author")


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    title: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = deferred(mapped_column(String(50)))
    author: Mapped[Author] = relationship(back_populates="books")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        for i in range(1, ROWS + 1):
            seed.add(
                Author(
                    id=i,
                    name=f"author {i}",
                    books=[Book(title=f"book {i}", summary="s")],
                )
            )
        seed.commit()
    with Session(engine) as session:
        yield session
    engine.dispose()


def _findings(log: object) -> list[object]:
    collector = log._collector  # type: ignore[attr-defined]
    return make_findings(
        detect(
            collector,
            relationship_threshold=2,
            repeat_threshold=5,
            column_load_threshold=5,
        )
    )


def _report(session: Session, work: object, **thresholds: int) -> str:
    """The N+1 report for ``work(session)``, or an empty string."""
    with record_queries() as log:
        work(session)  # type: ignore[operator]
    try:
        log.assert_no_nplusone(**thresholds)
    except NPlusOneDetected as exc:
        return str(exc)
    return ""


def _deferred_loop(session: Session) -> None:
    for book in session.scalars(select(Book)):
        _ = book.summary


# ---------------------------------------------------------------- deferred


def test_a_deferred_column_loop_gets_the_undefer_hint(session: Session) -> None:
    report = _report(session, _deferred_loop)
    assert "Book.summary was loaded on its own 6 times for 6 instances" in report
    assert "select(Book).options(undefer(Book.summary))" in report
    assert "remove defer(), load_only() or deferred()" in report
    assert "SELECT book.summary" in report
    # the wrong advice it used to get
    assert "IN (" not in report
    assert "same statement ran" not in report


def test_a_deferred_column_loop_is_one_finding_of_its_own_kind(
    session: Session,
) -> None:
    with record_queries() as log:
        _deferred_loop(session)
    (finding,) = _findings(log)
    assert finding.kind == "column-load"  # type: ignore[attr-defined]
    assert finding.label == "Book.summary"  # type: ignore[attr-defined]
    assert finding.count == ROWS  # type: ignore[attr-defined]
    assert finding.function == "_deferred_loop"  # type: ignore[attr-defined]


def test_undefer_loads_it_with_the_query(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book).options(undefer(Book.summary))):
            _ = book.summary

    assert _report(session, work) == ""


def test_a_few_deferred_loads_are_not_reported_by_default(session: Session) -> None:
    def work(s: Session) -> None:
        for book in list(s.scalars(select(Book)))[:4]:
            _ = book.summary

    assert _report(session, work) == ""
    assert "Book.summary" in _report(session, work, column_load_threshold=4)


def test_the_threshold_counts_instances_not_loads(session: Session) -> None:
    def work(s: Session) -> None:
        book = s.scalars(select(Book)).first()
        assert book is not None
        for _ in range(ROWS):
            s.expire(book, ["summary"])
            _ = book.summary

    assert _report(session, work) == ""


def test_load_only_leaves_columns_that_load_one_by_one(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book).options(load_only(Book.id))):
            _ = book.title

    report = _report(session, work)
    assert "Book.title was loaded on its own" in report
    assert "undefer(Book.title)" in report


def test_defer_option_gets_the_deferred_hint(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book).options(defer(Book.title))):
            _ = book.title

    assert "undefer(Book.title)" in _report(session, work)


def test_deferred_loads_do_not_count_as_a_repeated_statement(
    session: Session,
) -> None:
    with record_queries() as log:
        _deferred_loop(session)
    assert [f.kind for f in _findings(log)] == ["column-load"]  # type: ignore[attr-defined]
    assert sum(1 for q in log if "book.summary" in q.sql) == ROWS


# ----------------------------------------------------------------- expired


def _expired_loop(session: Session) -> None:
    books = session.scalars(select(Book)).all()
    session.commit()
    for book in books:
        _ = book.title


def test_attributes_refreshed_after_commit_get_the_expire_on_commit_hint(
    session: Session,
) -> None:
    report = _report(session, _expired_loop)
    assert "Book had its columns reloaded 6 times for 6 instances" in report
    assert "after commit(), expire() or expire_all()" in report
    assert "expire_on_commit=False" in report
    assert "IN (" not in report
    assert "same statement ran" not in report


def test_expired_loads_are_one_finding_labelled_by_cause(session: Session) -> None:
    with record_queries() as log:
        _expired_loop(session)
    (finding,) = _findings(log)
    assert finding.kind == "column-load"  # type: ignore[attr-defined]
    assert finding.label == "Book (expired)"  # type: ignore[attr-defined]


def test_expire_on_commit_false_needs_no_reload() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        s.add_all(
            Author(id=i, name="a", books=[Book(title="t", summary="s")])
            for i in range(1, ROWS + 1)
        )
        s.commit()
        assert _report(s, _expired_loop) == ""
    engine.dispose()


def test_one_instance_after_commit_is_not_a_finding(session: Session) -> None:
    def work(s: Session) -> None:
        book = s.scalars(select(Book)).first()
        s.commit()
        assert book is not None
        _ = book.title

    assert _report(session, work) == ""


def test_expire_all_is_an_expired_load(session: Session) -> None:
    def work(s: Session) -> None:
        books = s.scalars(select(Book)).all()
        s.expire_all()
        for book in books:
            _ = book.title

    assert "expire_on_commit=False" in _report(session, work)


def test_expired_instances_that_load_a_relationship_too(session: Session) -> None:
    # The refresh and the lazy load are told apart: one finding of each kind.
    def work(s: Session) -> None:
        authors = s.scalars(select(Author)).all()
        s.commit()
        for author in authors:
            _ = author.books

    with record_queries() as log:
        work(session)
    kinds = sorted(f.kind for f in _findings(log))  # type: ignore[attr-defined]
    assert kinds == ["column-load", "relationship-load"]


def test_session_refresh_in_a_loop_gets_a_refresh_hint(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book)).all():
            s.refresh(book)

    report = _report(session, work)
    assert "reloaded with session.refresh() 6 times" in report
    assert "populate_existing=True" in report


def test_a_named_session_refresh_in_a_loop_is_a_refresh(session: Session) -> None:
    # refresh(obj, names) expires the names first; it is still a refresh
    def work(s: Session) -> None:
        for book in s.scalars(select(Book)).all():
            s.refresh(book, ["title"])

    report = _report(session, work)
    assert "Book was reloaded with session.refresh() 6 times" in report
    assert "label: Book (refresh)" in report
    assert "expire_on_commit" not in report


# ------------------------------------------------------ relationship hints


def test_scalar_relationship_hint_is_joinedload(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book)):
            _ = book.author.name

    report = _report(session, work)
    assert "Book.author was lazy-loaded" in report
    assert "select(Book).options(joinedload(Book.author))" in report
    assert "selectinload" not in report


def test_collection_relationship_hint_is_selectinload(session: Session) -> None:
    def work(s: Session) -> None:
        for author in s.scalars(select(Author)):
            _ = author.books

    report = _report(session, work)
    assert "select(Author).options(selectinload(Author.books))" in report
    assert "joinedload" not in report


def test_eager_loading_is_not_reported(session: Session) -> None:
    def work(s: Session) -> None:
        for book in s.scalars(select(Book).options(joinedload(Book.author))):
            _ = book.author.name
        for author in s.scalars(select(Author).options(selectinload(Author.books))):
            _ = author.books

    assert _report(session, work) == ""


# ---------------------------------------------------------------- plugin

MODELS = (
    Path(__file__).read_text().split("ROWS = 6\n", 1)[1].split("@pytest.fixture")[0]
)

PLUGIN_BODY = (
    """
from __future__ import annotations

import pytest
from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, deferred, mapped_column, relationship,
)
from sqlalchemy.pool import StaticPool
"""
    + MODELS
    + """

def seed():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for i in range(1, 7):
            s.add(Author(id=i, name="a", books=[Book(title="t", summary="s")]))
        s.commit()
    return engine

{marker}
def test_loop():
    with Session(seed()) as s:
        for book in s.scalars(select(Book)):
            book.summary
"""
)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    return pytester


def _write(project: pytest.Pytester, marker: str = "") -> None:
    project.makepyfile(test_a=PLUGIN_BODY.replace("{marker}", marker))


def test_the_plugin_fails_a_deferred_loop_with_the_new_hint(
    project: pytest.Pytester,
) -> None:
    _write(project)
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*N+1 detected*", "*Book.summary was loaded on its own 6 times*", "*undefer*"]
    )
    result.stdout.no_fnmatch_line("*IN (...)*")


def test_warn_mode_warns(project: pytest.Pytester) -> None:
    _write(project)
    result = project.runpytest("--queryguard-mode=warn")
    result.assert_outcomes(passed=1, warnings=1)


def test_the_ini_option_lowers_the_threshold(project: pytest.Pytester) -> None:
    _write(project)
    project.makeini("[pytest]\nqueryguard_column_load_threshold = 7\n")
    project.runpytest().assert_outcomes(passed=1)
    project.makeini("[pytest]\nqueryguard_column_load_threshold = 2\n")
    project.runpytest().assert_outcomes(failed=1)


@pytest.mark.parametrize("value", ["1", "x", "0"])
def test_the_ini_threshold_must_be_an_integer_of_at_least_two(
    project: pytest.Pytester, value: str
) -> None:
    _write(project)
    project.makeini(f"[pytest]\nqueryguard_column_load_threshold = {value}\n")
    result = project.runpytest()
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*queryguard_column_load_threshold must be*"])


def test_ini_ignore_by_kind_and_label(project: pytest.Pytester) -> None:
    _write(project)
    project.makeini("[pytest]\nqueryguard_ignore = column-load Book.summary\n")
    project.runpytest().assert_outcomes(passed=1)


def test_the_marker_ignores_by_kind(project: pytest.Pytester) -> None:
    _write(project, '@pytest.mark.allow_nplusone(kind="column-load")')
    project.runpytest().assert_outcomes(passed=1)


def test_the_marker_ignores_by_label(project: pytest.Pytester) -> None:
    _write(project, '@pytest.mark.allow_nplusone("Book.*")')
    project.runpytest().assert_outcomes(passed=1)


def test_a_marker_for_another_kind_does_not_ignore_it(
    project: pytest.Pytester,
) -> None:
    _write(project, '@pytest.mark.allow_nplusone(kind="repeated-statement")')
    project.runpytest().assert_outcomes(failed=1)


def test_the_event_cap_is_noted_for_column_loads(project: pytest.Pytester) -> None:
    _write(project)
    project.makeini(
        "[pytest]\nqueryguard_max_events = 2\nqueryguard_column_load_threshold = 2\n"
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*only the first 2 statements*"])
