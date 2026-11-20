"""The experimental queryset-len check: len() or bool() of a queryset whose
rows are never read (queryguard_queryset_len, docs/django.md)."""

from __future__ import annotations

import gc
import json
import os
import pickle
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.db.models import Prefetch, QuerySet
from django.template import Context, Template

from pytest_queryguard import blocks, callsite, ignore, queryset_len
from pytest_queryguard.findings import Finding

from .helpers import measure
from .library.models import Author, Book

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]


@pytest.fixture(autouse=True)
def application_code() -> Iterator[None]:
    """The functions of this module count as the application's: with no
    rootdir, only conftest.py and factory modules are test code."""
    previous = callsite.swap_test_code(callsite.TestCode())
    try:
        yield
    finally:
        callsite.swap_test_code(previous)


def _findings(
    run: Callable[[], object], sieve: blocks.Sieve | None = None
) -> list[Finding]:
    """The findings of the check for what ``run`` does, in a measurement."""
    queryset_len.enable()
    try:
        with measure():
            tracker = queryset_len.start()
            try:
                run()
            finally:
                queryset_len.stop(tracker)
        gc.collect()  # querysets kept by a reference cycle are gone too
        return queryset_len.findings(tracker, sieve=sieve)
    finally:
        queryset_len.disable()


# --- reported ---------------------------------------------------------------


def count_books() -> int:
    books = Book.objects.filter(author_id=1)
    return len(books)


def has_books() -> bool:
    return bool(Book.objects.filter(author_id=1))


def notify_if_any() -> str:
    books = Book.objects.filter(author_id=2)
    if books:
        return "sent"
    return "none"


def none_left() -> bool:
    return not Book.objects.filter(pages__gt=10_000)


def test_len_alone_is_reported() -> None:
    (finding,) = _findings(count_books)
    assert finding.kind == "queryset-len"
    assert finding.label == "len(Book)"
    assert finding.function == "count_books"
    assert finding.count == 1
    assert "len() of a Book queryset loaded all its rows (3 rows)" in finding.message
    assert "qs.count()" in finding.message
    assert "statement: SELECT" in finding.message
    assert "kind: queryset-len  label: len(Book)" in finding.message


def test_bool_alone_is_reported() -> None:
    (finding,) = _findings(has_books)
    assert (finding.label, finding.function) == ("bool(Book)", "has_books")
    assert "qs.exists()" in finding.message


def test_if_qs_alone_is_reported() -> None:
    (finding,) = _findings(notify_if_any)
    assert (finding.label, finding.function) == ("bool(Book)", "notify_if_any")


def test_an_empty_result_is_reported_too() -> None:
    (finding,) = _findings(none_left)
    assert finding.label == "bool(Book)"


def test_one_line_run_three_times_is_one_finding() -> None:
    def run() -> None:
        for _ in range(3):
            count_books()

    (finding,) = _findings(run)
    assert finding.count == 3
    assert "3 times" in finding.message


# --- not reported -----------------------------------------------------------


def _held(**filters: Any) -> Any:
    """A queryset on an attribute: no local variable holds it, so only a read
    of its rows keeps it out of the findings (see _handed_over)."""
    return SimpleNamespace(books=Book.objects.filter(**filters).order_by("pk"))


def test_the_held_queryset_alone_is_reported() -> None:
    def run() -> object:
        held = _held(author_id=1)
        return len(held.books), bool(_held(author_id=2).books)

    assert sorted(f.label for f in _findings(run)) == ["bool(Book)", "len(Book)"]


def test_len_then_iterate_is_not_reported() -> None:
    def run() -> list[str]:
        held = _held(author_id=1)
        if len(held.books) > 2:
            return [b.title for b in held.books]
        return []

    assert _findings(run) == []


def test_if_qs_then_iterate_is_not_reported() -> None:
    def run() -> list[str]:
        held = _held(author_id=1)
        if held.books:
            return [b.title for b in held.books]
        return []

    assert _findings(run) == []


def test_bool_then_index_or_first_is_not_reported() -> None:
    def run() -> object:
        held, other = _held(author_id=1), _held(author_id=2)
        if held.books and other.books:
            return held.books[0], other.books.first()  # first() slices the cache
        return None

    assert _findings(run) == []


def test_list_and_membership_are_not_reported() -> None:
    def run() -> object:
        held, other = _held(author_id=1), _held(author_id=2)
        titles = list(Book.objects.filter(author_id=2))  # len() then __iter__
        len(held.books)
        bool(other.books)
        return titles, held.books.contains(Book(pk=1)), Book(pk=7) in other.books

    assert _findings(run) == []


def test_pickling_reads_the_rows() -> None:
    def run() -> bytes:
        held = _held(author_id=1)
        if held.books:
            return pickle.dumps(held.books)
        return b""

    assert _findings(run) == []


def test_count_and_exists_are_not_reported() -> None:
    def run() -> object:
        return (
            Book.objects.filter(author_id=1).count(),
            Book.objects.filter(author_id=1).exists(),
        )

    assert _findings(run) == []


def test_an_evaluated_queryset_is_not_reported() -> None:
    def run() -> object:
        books = Book.objects.filter(author_id=1)
        titles = [b.title for b in books]
        return len(books), bool(books), titles

    assert _findings(run) == []


def test_a_sliced_queryset_is_not_reported() -> None:
    def run() -> object:
        return bool(Book.objects.all()[:1]), len(Book.objects.all()[:5])

    assert _findings(run) == []


def test_an_empty_queryset_runs_nothing_and_is_not_reported() -> None:
    def run() -> bool:
        return bool(Book.objects.none())

    assert _findings(run) == []


def test_prefetched_relations_are_not_reported() -> None:
    def run() -> object:
        authors = Author.objects.prefetch_related(
            Prefetch("books", queryset=Book.objects.order_by("pk"))
        )
        return [(len(a.books.all()), bool(a.books.all())) for a in authors]

    assert _findings(run) == []


def test_djangos_own_len_is_not_reported() -> None:
    # get() runs len() on a clone and reads its cache directly
    def run() -> object:
        return Book.objects.get(pk=1)

    assert _findings(run) == []


def test_a_template_that_tests_and_iterates_is_not_reported() -> None:
    template = Template(
        "{% if books %}{% for b in books %}{{ b.title }}{% endfor %}{% endif %}"
    )

    def run() -> str:
        return template.render(Context({"books": Book.objects.filter(author_id=1)}))

    assert _findings(run) == []


def test_a_template_that_only_tests_is_not_reported() -> None:
    # Django calls bool(): it may read the cache directly, so it is left out
    template = Template("{% if books %}some{% endif %}")

    def run() -> str:
        return template.render(Context({"books": Book.objects.filter(author_id=1)}))

    assert _findings(run) == []


def check_and_return() -> Any:
    books = Book.objects.filter(author_id=1)
    if not books:
        raise LookupError("no books")
    return books


def test_a_queryset_returned_after_the_check_is_not_reported() -> None:
    def run() -> bool:
        return check_and_return() is not None

    assert _findings(run) == []


def test_a_variable_read_again_is_not_reported() -> None:
    # missed on purpose: a variable read twice may be handed to other code;
    # first() of an unordered queryset reads no cached row, but is such a read
    def first_if_any() -> Any:
        authors = Author.objects.filter(pk__lte=3).order_by()
        if authors:
            return authors.first()
        return None

    assert _findings(first_if_any) == []


def test_a_queryset_passed_in_is_not_reported() -> None:
    def any_of(books: Any) -> bool:
        return bool(books)

    def run() -> bool:
        return any_of(Book.objects.filter(author_id=1))

    assert _findings(run) == []


def test_a_queryset_still_referenced_when_the_test_ends_is_not_reported() -> None:
    kept: list[Any] = []

    def run() -> None:
        kept.append(Book.objects.filter(author_id=1))
        if kept[0]:
            pass

    assert _findings(run) == []
    assert kept


def test_test_code_is_not_reported() -> None:
    # the default classification: this module is under tests/
    callsite.swap_test_code(callsite.TestCode(str(Path(__file__).parents[2])))
    assert _findings(count_books) == []


def test_nothing_is_noted_while_the_check_is_off() -> None:
    with measure():
        assert queryset_len.current is None
        count_books()
    assert queryset_len.current is None


def test_aliases_add_up_their_reads() -> None:
    def run() -> Any:
        alias = books = Book.objects.filter(author_id=1)
        if not alias:
            raise LookupError("no books")
        return books

    assert _findings(run) == []


def test_a_read_combined_with_a_store_counts() -> None:
    # 3.13+ compiles "out = books" after "n = len(books)" into STORE_FAST_LOAD_FAST
    def run() -> Any:
        books = Book.objects.filter(author_id=1)
        n = len(books)
        out = books
        return n, out

    assert _findings(run) == []


class OwnIterQuerySet(QuerySet):  # type: ignore[type-arg]
    def __iter__(self) -> Iterator[Any]:
        self._fetch_all()
        yield from self._result_cache  # type: ignore[misc]


def test_a_subclass_that_reads_its_rows_itself_is_not_reported() -> None:
    def run() -> list[str]:
        held = SimpleNamespace(books=OwnIterQuerySet(Book).filter(author_id=1))
        if held.books:
            return [b.title for b in held.books]
        return []

    assert _findings(run) == []


def test_bool_then_count_suggests_count() -> None:
    def with_len() -> int:
        held = _held(author_id=1)
        if held.books:
            return len(held.books)
        return 0

    def with_count() -> int:
        held = _held(author_id=1)
        return held.books.count() if held.books else 0

    for run in (with_len, with_count):
        (finding,) = _findings(run)
        assert finding.label == "len(Book)"
        assert "qs.count()" in finding.message


def test_the_statement_is_the_querysets_select() -> None:
    (finding,) = _findings(count_books)
    statement = finding.message.split("statement: ", 1)[1]
    assert statement.startswith("SELECT")
    assert "library_book" in statement.splitlines()[0]


# --- ignore() ---------------------------------------------------------------


def test_an_ignore_block_leaves_it_out() -> None:
    def run() -> None:
        with ignore("queryset-len"):
            count_books()

    sieve = blocks.Sieve()
    assert _findings(run, sieve) == []
    assert sieve.used


def test_a_block_for_another_kind_does_not() -> None:
    def run() -> None:
        with ignore("relationship-load"):
            count_books()

    assert [f.label for f in _findings(run, blocks.Sieve())] == ["len(Book)"]


def test_async_iteration_reads_the_rows() -> None:
    import asyncio

    async def titles(holder: Any) -> list[str]:
        return [b.title async for b in holder.books]

    def run() -> list[str]:
        holder = SimpleNamespace(books=Book.objects.filter(author_id=1))
        len(holder.books)  # no local variable: nothing hands it over
        return asyncio.run(titles(holder))

    def without_reading() -> int:
        holder = SimpleNamespace(books=Book.objects.filter(author_id=1))
        return len(holder.books)

    assert _findings(run) == []
    assert [f.function for f in _findings(without_reading)] == ["without_reading"]


# --- the plugin, end to end in a pytest-django session ------------------------

SHOP = """
from tests.django_project.library.models import Book


def count_books():
    books = Book.objects.filter(author_id=1)
    return len(books)
"""

SESSION = """
import pytest
import shop
from tests.django_project.library.models import Book
from tests.django_project.library.seed import seed


@pytest.fixture
def seeded(db):
    seed()


def test_counts(seeded):
    assert shop.count_books() == 3


@pytest.mark.queryguard_ignore("len(Book)", kind="queryset-len")
def test_marked(seeded):
    assert shop.count_books() == 3


def test_len_in_the_test(seeded):
    assert len(Book.objects.filter(author_id=1)) == 3
"""


@pytest.fixture
def project(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> pytest.Pytester:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "tests.django_project.settings")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(root), os.environ.get("PYTHONPATH", "")])
    )
    pytester.makepyfile(shop=SHOP, test_shop=SESSION)
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:randomly", *args
    )


def test_the_check_is_off_by_default(project: pytest.Pytester) -> None:
    result = _run(project)
    result.assert_outcomes(passed=3)
    assert "Book queryset" not in result.stdout.str()


def test_fail_reports_the_application_line_only(project: pytest.Pytester) -> None:
    result = _run(
        project, "-o", "queryguard_queryset_len=fail", "--queryguard-sarif=out.sarif"
    )
    # the marker leaves out test_marked, and test code is never reported
    result.assert_outcomes(passed=2, failed=1)
    out = result.stdout.str()
    assert "queryguard: queryset loaded only to count it (experimental)" in out
    assert "len() of a Book queryset loaded all its rows (3 rows)" in out
    assert "first at shop.py:6 in count_books" in out
    assert "kind: queryset-len  label: len(Book)" in out
    assert "FAILED test_shop.py::test_counts" in out
    sarif = json.loads((project.path / "out.sarif").read_text(encoding="utf-8"))
    run = sarif["runs"][0]
    rules = [rule["id"] for rule in run["tool"]["driver"]["rules"]]
    assert "queryguard/queryset-len" in rules
    (found,) = run["results"]
    assert found["ruleId"] == "queryguard/queryset-len"
    assert found["level"] == "error"


def test_warn_only_warns(project: pytest.Pytester) -> None:
    result = _run(project, "-o", "queryguard_queryset_len=warn")
    result.assert_outcomes(passed=3, warnings=1)
    assert "len() of a Book queryset" in result.stdout.str()


def test_the_ignore_option_and_the_baseline(project: pytest.Pytester) -> None:
    on = ("-o", "queryguard_queryset_len=fail")
    ignored = _run(project, *on, "-o", "queryguard_ignore=queryset-len len(Book*)")
    ignored.assert_outcomes(passed=3)

    baseline = project.path / "baseline.json"
    _run(
        project,
        *on,
        f"--queryguard-baseline={baseline}",
        "--queryguard-update-baseline",
    )
    entries = json.loads(baseline.read_text(encoding="utf-8"))["findings"]
    assert entries == [
        {
            "kind": "queryset-len",
            "label": "len(Book)",
            "file": "shop.py",
            "function": "count_books",
        }
    ]
    _run(project, *on, f"--queryguard-baseline={baseline}").assert_outcomes(passed=3)
