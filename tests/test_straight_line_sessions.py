"""Sessions begun from lines of their own count on their own (``queryguard_scope = test``).

A store that opens a Session per method, called at separate lines with no loop
(MLflow's ``test_set_experiment_tag``), runs its shared lookup once per call:
each call is a unit of work, not a pass of a loop. A Session remembers the
application lines it began from; one begun from lines that no other Session of
the measurement began from is counted on its own. Sessions begun from the same
lines (a loop, a comprehension, a thread pool, ``asyncio.gather``) still add
up, and so do the Sessions of every other pass of a loop together, whatever
lines they began from, so a loop that alternates between two stacks is not
split in two.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pytest_queryguard import NPlusOneDetected, record_queries
from pytest_queryguard.callsite import find_entry
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import RelationshipFinding, RepeatFinding, detect
from pytest_queryguard.events import CallSite, RelationshipLoadEvent, StatementEvent
from tests.models import Base, Customer, Order

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

SITE = CallSite(filename="app.py", lineno=10, function="lookup")
BY_ID = "SELECT * FROM orgs WHERE id = ?"
MODELS = (Path(__file__).parent / "models.py").read_text()
LOOP = (("app.py", 10), ("test_x.py", 5))


def _detect(c: QueryCollector, scope: str = "test") -> list[object]:
    return list(detect(c, relationship_threshold=2, repeat_threshold=5, scope=scope))


def _statements(c: QueryCollector, scopes: range, entry: object) -> None:
    for scope in scopes:
        c.statements.append(StatementEvent(BY_ID, SITE, None, scope=scope))
        if entry is not None:
            lines = entry(scope) if callable(entry) else entry
            c.note_entry(scope, lines)


# ------------------------------------------------------------- unit: rules


def test_sessions_begun_at_separate_lines_count_on_their_own() -> None:
    c = QueryCollector()
    _statements(c, range(1, 7), lambda n: (("app.py", 10), ("test_x.py", n)))
    assert _detect(c) == []


def test_sessions_begun_at_one_line_add_up() -> None:
    c = QueryCollector()
    _statements(c, range(1, 7), LOOP)
    (finding,) = _detect(c)
    assert isinstance(finding, RepeatFinding)
    assert (finding.count, finding.scopes) == (6, 6)


def test_every_repeated_entry_counts_together() -> None:
    # a loop that alternates between two stacks: three passes at each
    c = QueryCollector()
    _statements(c, range(1, 7), lambda n: (("app.py", 10), ("test_x.py", n % 2)))
    (finding,) = _detect(c)
    assert isinstance(finding, RepeatFinding)
    assert finding.count == 6


def test_a_short_loop_does_not_add_up_with_separate_calls() -> None:
    c = QueryCollector()
    _statements(c, range(1, 4), LOOP)
    _statements(c, range(4, 7), lambda n: (("app.py", 10), ("test_x.py", 100 + n)))
    assert _detect(c) == []


def test_a_session_of_its_own_still_reports_its_own_loop() -> None:
    c = QueryCollector()
    for _ in range(6):
        c.statements.append(StatementEvent(BY_ID, SITE, None, scope=1))
    c.note_entry(1, (("test_x.py", 3),))
    for scope in (2, 3):
        c.statements.append(StatementEvent(BY_ID, SITE, None, scope=scope))
        c.note_entry(scope, (("test_x.py", 100 + scope),))
    (finding,) = _detect(c)
    assert isinstance(finding, RepeatFinding)
    assert (finding.count, finding.scopes) == (6, 1)


def test_sessions_without_an_entry_add_up_as_before() -> None:
    c = QueryCollector()
    _statements(c, range(1, 6), None)
    (finding,) = _detect(c)
    assert isinstance(finding, RepeatFinding)
    assert finding.count == 5


def test_relationship_parents_of_separate_calls_are_no_n_plus_one() -> None:
    parent = CallSite(filename="store.py", lineno=4, function="_get")
    from pytest_queryguard.events import ParentLoad

    def loads(entries: list[tuple[tuple[str, int], ...]]) -> QueryCollector:
        c = QueryCollector()
        for scope, entry in enumerate(entries, start=1):
            c.relationship_loads.append(
                RelationshipLoadEvent(
                    "Customer.orders",
                    ("customer", scope),
                    SITE,
                    scope=scope,
                    parent=ParentLoad(parent),
                )
            )
            c.note_entry(scope, entry)
        return c

    assert _detect(loads([(("t.py", 1),), (("t.py", 2),)])) == []
    (finding,) = _detect(loads([(("t.py", 1),), (("t.py", 1),)]))
    assert isinstance(finding, RelationshipFinding)
    assert finding.count == 2


def test_session_scope_is_unchanged() -> None:
    c = QueryCollector()
    _statements(c, range(1, 7), LOOP)
    assert _detect(c, "session") == []


def test_the_entries_kept_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytest_queryguard import collector as collector_module

    monkeypatch.setattr(collector_module, "MAX_ENTRIES", 3)
    c = QueryCollector()
    for scope in range(1, 6):
        c.note_entry(scope, (("test_x.py", scope),))
    assert c.unit_of(1) == 1
    assert c.unit_of(3) == 3
    assert c.unit_of(4) is None  # past the bound: counted with the others


def test_past_max_events_the_whole_test_counts_together() -> None:
    c = QueryCollector(max_events=6)
    _statements(c, range(1, 7), lambda n: (("app.py", 10), ("test_x.py", n)))
    assert _detect(c) == []
    c.statement_count = 7  # one more statement, counted but not stored
    (finding,) = _detect(c)
    assert isinstance(finding, RepeatFinding)


def _strict_collector(ignored: set[str]) -> QueryCollector:
    """A strict collector whose findings located in ``ignored`` functions pass."""
    c = QueryCollector()
    c.strict_threshold = 2

    def strict(
        load: RelationshipLoadEvent, first: CallSite | None, parents: int, other: bool
    ) -> BaseException | None:
        site = first or load.callsite
        if site is not None and site.function in ignored:
            return None
        return NPlusOneDetected(f"{parents} parents")

    c.strict = strict
    return c


def _lazy(c: QueryCollector, scope: int, parent: int, reader: str) -> object:
    from pytest_queryguard.events import ParentLoad

    load = RelationshipLoadEvent(
        "Customer.orders",
        ("customer", parent),
        CallSite(filename="app.py", lineno=parent, function=reader),
        scope=scope,
        parent=ParentLoad(CallSite(filename="store.py", lineno=4, function="_get")),
    )
    # in the listeners' order: the check, then the load once its SELECT ran
    error = c.strict_check(load)
    c.add_relationship_load(load)
    return error


def test_strict_counts_each_unit_on_its_own() -> None:
    c = _strict_collector(set())
    for scope in (1, 2):
        c.note_entry(scope, (("t.py", scope),))
    assert _lazy(c, 1, 1, "first") is None
    assert _lazy(c, 2, 2, "second") is None  # another call, not a second pass
    for scope in (3, 4):
        c.note_entry(scope, (("t.py", 99),))
    assert _lazy(c, 3, 3, "loop") is None
    assert isinstance(_lazy(c, 4, 4, "loop"), NPlusOneDetected)


def test_strict_an_ignored_unit_does_not_silence_the_others() -> None:
    c = _strict_collector({"ignored"})
    c.note_entry(1, (("t.py", 1),))
    assert _lazy(c, 1, 1, "ignored") is None
    assert _lazy(c, 1, 2, "ignored") is None  # an N+1 of its own, ignored
    for scope in (2, 3):
        c.note_entry(scope, (("t.py", 99),))
    assert _lazy(c, 2, 3, "loop") is None
    assert isinstance(_lazy(c, 3, 4, "loop"), NPlusOneDetected)


def test_a_unit_whose_lines_repeat_later_joins_the_shared_count() -> None:
    c = _strict_collector(set())
    c.note_entry(1, LOOP)
    assert _lazy(c, 1, 1, "loop") is None  # a unit of its own so far
    c.note_entry(2, LOOP)  # the same lines again: both are passes of a loop
    assert c.unit_of(1) is None
    assert isinstance(_lazy(c, 2, 2, "loop"), NPlusOneDetected)


def test_past_the_bound_a_known_entry_still_counts_as_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pytest_queryguard import collector as collector_module

    monkeypatch.setattr(collector_module, "MAX_ENTRIES", 2)
    c = QueryCollector()
    c.note_entry(1, (("test_x.py", 1),))
    c.note_entry(2, LOOP)  # the first pass of a loop, the bound reached
    c.note_entry(3, LOOP)  # its second pass, past the bound
    assert c.unit_of(2) is None
    assert c.unit_of(1) == 1


def test_strict_locates_a_unit_at_its_own_load() -> None:
    sites: list[CallSite | None] = []
    c = _strict_collector(set())
    inner = c.strict
    assert inner is not None

    def strict(
        load: RelationshipLoadEvent, first: CallSite | None, parents: int, other: bool
    ) -> BaseException | None:
        sites.append(first)
        return inner(load, first, parents, other)

    c.strict = strict
    for scope in (1, 2):
        c.note_entry(scope, (("t.py", scope),))
    assert _lazy(c, 1, 1, "first") is None
    # an earlier load of unit 2 whose reading line is unknown
    from pytest_queryguard.events import ParentLoad

    unknown = RelationshipLoadEvent(
        "Customer.orders",
        ("customer", 2),
        None,
        scope=2,
        parent=ParentLoad(CallSite(filename="store.py", lineno=4, function="_get")),
    )
    assert c.strict_check(unknown) is None
    c.add_relationship_load(unknown)
    assert isinstance(_lazy(c, 2, 3, "second"), NPlusOneDetected)
    assert sites[-1] is not None
    assert sites[-1].function == "second"  # not unit 1's "first"


# ------------------------------------------------------- unit: find_entry


def _lookup() -> object:
    return find_entry()


def _recurse(depth: int) -> object:
    if depth == 0:
        return _lookup()
    return _recurse(depth - 1)


def test_the_entry_is_the_application_lines_up_to_the_test() -> None:
    first = _lookup()
    second = _lookup()
    assert first != second  # two lines of this test
    in_loop = [_lookup() for _ in range(3)]
    assert in_loop[0] == in_loop[1] == in_loop[2]
    assert isinstance(first, tuple)
    assert first[0][0] == __file__
    assert all("_pytest" not in filename for filename, *_ in first)


def test_recursion_does_not_make_each_depth_a_new_entry() -> None:
    shallow, deep = [_recurse(depth) for depth in (1, 4)]
    assert shallow == deep


# ----------------------------------------------------------- record_queries


def _engine():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for c in range(1, 9):
            s.add(Customer(id=c, name=f"customer {c}", orders=[Order(id=10 * c)]))
        s.commit()
    return engine


def _name(engine, customer_id: int) -> str:  # type: ignore[no-untyped-def]
    with Session(engine) as s:
        return s.scalars(select(Customer).where(Customer.id == customer_id)).one().name


def _orders(maker, customer_id: int) -> int:  # type: ignore[no-untyped-def]
    with maker() as s:
        customer = s.scalars(select(Customer).where(Customer.id == customer_id)).one()
        return len(customer.orders)


def test_assert_no_nplusone_counts_separate_calls_on_their_own() -> None:
    engine = _engine()
    maker = sessionmaker(engine)
    with record_queries() as log:
        _name(engine, 1)
        _name(engine, 2)
        _name(engine, 3)
        _name(engine, 4)
        _name(engine, 5)
        _orders(maker, 1)
        _orders(maker, 2)
    log.assert_no_nplusone()


def test_assert_no_nplusone_still_reports_a_loop() -> None:
    engine = _engine()
    maker = sessionmaker(engine)
    with record_queries() as log:
        for i in range(1, 6):
            _name(engine, i)
    with pytest.raises(NPlusOneDetected, match="repeated-statement"):
        log.assert_no_nplusone()
    with record_queries() as log:
        for i in range(1, 3):
            _orders(maker, i)
    with pytest.raises(NPlusOneDetected, match="Customer.orders"):
        log.assert_no_nplusone()


# ------------------------------------------------------------------- pytester

CASES = """
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from qgmodels import Base, Customer, Order


def make_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for c in range(1, 9):
            s.add(Customer(id=c, name=f"customer {c}", orders=[Order(id=c * 10)]))
        s.commit()
    return engine


class Store:
    def __init__(self, engine):
        self.engine = engine

    def _get(self, session, customer_id):
        return session.scalars(select(Customer).where(Customer.id == customer_id)).one()

    def name(self, customer_id):
        with Session(self.engine) as s:
            return self._get(s, customer_id).name

    def rename(self, customer_id, name):
        with Session(self.engine) as s:
            self._get(s, customer_id).name = name
            s.commit()

    def order_count(self, customer_id):
        with Session(self.engine) as s:
            return len(self._get(s, customer_id).orders)

    def chain(self, customer_id):
        with Session(self.engine) as s:
            self._get(s, customer_id)
        if customer_id < 6:
            self.chain(customer_id + 1)


def rename_twice(store, customer_id):
    store.rename(customer_id, "a")
    store.rename(customer_id, "b")


def test_store_calls_at_separate_lines():
    store = Store(make_engine())
    store.rename(1, "Ada")
    store.rename(2, "Grace")
    assert store.name(1) == "Ada"
    assert store.name(2) == "Grace"
    store.rename(1, "Ada L.")
    assert store.name(1) == "Ada L."


def test_lazy_loads_at_separate_lines():
    store = Store(make_engine())
    assert store.order_count(1) == 1
    assert store.order_count(2) == 1


def test_straight_line_calls_inside_a_helper():
    store = Store(make_engine())
    rename_twice(store, 1)
    rename_twice(store, 2)
    rename_twice(store, 3)


def test_short_loop_and_separate_calls():
    store = Store(make_engine())
    for i in range(1, 4):
        store.name(i)
    store.name(4)
    store.name(5)
    store.name(6)


def test_checkouts_at_separate_lines():
    engine = make_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM customers WHERE id = 1"))
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM customers WHERE id = 2"))
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM customers WHERE id = 3"))
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM customers WHERE id = 4"))
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM customers WHERE id = 5"))


def test_store_calls_in_a_loop():
    store = Store(make_engine())
    for i in range(1, 6):
        store.name(i)


def test_store_calls_in_a_comprehension():
    store = Store(make_engine())
    assert len([store.name(i) for i in range(1, 6)]) == 5


def test_two_store_methods_alternating_in_a_loop():
    store = Store(make_engine())
    for i in range(1, 7):
        if i % 2:
            store.name(i)
        else:
            store.order_count(i)


def test_a_helper_with_separate_calls_in_a_loop():
    store = Store(make_engine())
    for i in range(1, 4):
        rename_twice(store, i)


def test_lazy_loads_in_a_loop():
    store = Store(make_engine())
    for i in range(1, 3):
        store.order_count(i)


def test_recursion_with_a_session_per_call():
    Store(make_engine()).chain(1)


def test_thread_pool_with_a_session_per_task():
    store = Store(make_engine())
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(store.name, range(1, 6)))
"""

PASSING = {
    "test_store_calls_at_separate_lines",
    "test_lazy_loads_at_separate_lines",
    "test_straight_line_calls_inside_a_helper",
    "test_short_loop_and_separate_calls",
    "test_checkouts_at_separate_lines",
}
FAILING = {
    "test_store_calls_in_a_loop",
    "test_store_calls_in_a_comprehension",
    "test_two_store_methods_alternating_in_a_loop",
    "test_a_helper_with_separate_calls_in_a_loop",
    "test_lazy_loads_in_a_loop",
    "test_recursion_with_a_session_per_call",
    "test_thread_pool_with_a_session_per_task",
}

ASYNC_CASES = """
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from qgmodels import Base, Customer


async def make_maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def lookup(maker, i):
    async with maker() as s:
        await s.execute(select(Customer).where(Customer.id == i))


def test_async_calls_at_separate_lines():
    async def main():
        engine, maker = await make_maker()
        await lookup(maker, 1)
        await lookup(maker, 2)
        await lookup(maker, 3)
        await lookup(maker, 4)
        await lookup(maker, 5)
        await engine.dispose()
    asyncio.run(main())


def test_async_calls_in_a_loop():
    async def main():
        engine, maker = await make_maker()
        for i in range(5):
            await lookup(maker, i)
        await engine.dispose()
    asyncio.run(main())


def test_async_gather():
    async def main():
        engine, maker = await make_maker()
        await asyncio.gather(*(lookup(maker, i) for i in range(5)))
        await engine.dispose()
    asyncio.run(main())
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


def _outcomes(pytester: pytest.Pytester, *args: str) -> tuple[set[str], set[str]]:
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rfp", *args)
    outcomes: dict[str, set[str]] = {"PASSED": set(), "FAILED": set()}
    for line in result.outlines:
        word = line.split(" ")[0]
        if word in outcomes and "::" in line:
            outcomes[word].add(line.split("::")[-1].split(" ")[0])
    return outcomes["PASSED"], outcomes["FAILED"]


def test_separate_calls_pass_and_loops_fail(project: pytest.Pytester) -> None:
    project.makepyfile(test_cases=CASES)
    passed, failed = _outcomes(project)
    assert (passed, failed) == (PASSING, FAILING)


def test_strict_mode_counts_separate_calls_on_their_own(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_cases=CASES)
    project.makeini("[pytest]\nqueryguard_strict = true\n")
    passed, failed = _outcomes(project, "-k", "lazy_loads")
    assert passed == {"test_lazy_loads_at_separate_lines"}
    assert failed == {"test_lazy_loads_in_a_loop"}


def test_async_calls_at_separate_lines_pass(project: pytest.Pytester) -> None:
    pytest.importorskip("greenlet")
    pytest.importorskip("aiosqlite")
    project.makepyfile(test_async=ASYNC_CASES)
    passed, failed = _outcomes(project)
    assert passed == {"test_async_calls_at_separate_lines"}
    assert failed == {"test_async_calls_in_a_loop", "test_async_gather"}
