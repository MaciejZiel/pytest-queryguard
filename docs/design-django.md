# Design note: Django ORM support (research, not a plan of record)

Status: research for a release after 1.0. No code. Every number below was read
on **2026-11-07** unless a row says otherwise; download counts change daily and
are dominated by CI, so read them as orders of magnitude.

**In short:** go, after 1.0, with a narrow first release. The Django market for
query-checking tools is roughly ten times the SQLAlchemy one, the oldest tool
(nplusone) is abandoned and breaks on Django 6.1, and no Django tool has a
baseline, SARIF, a budget file, a pull-request comparison or a plan-based
missing-index check. About three quarters of the code is not tied to
SQLAlchemy. The 1.0 public API needs no breaking change; two small changes
before 1.0 make Django a pure addition in 1.x (see
[Before 1.0](#what-to-change-before-10)).

## 1. Market

### How much of nplusone is Django

nplusone ([jmcarp/nplusone](https://github.com/jmcarp/nplusone)) has one
distribution for Django, SQLAlchemy, Flask-SQLAlchemy and Peewee, so PyPI
cannot split its downloads. GitHub code search (REST API, public default
branches only; the counts are GitHub's estimates) gives the split of the
integration a project actually imports:

| Query | Files |
|---|---|
| `"nplusone.ext.django"` | 339 |
| `"nplusone.ext.flask_sqlalchemy"` | 31 |
| `"nplusone.ext.sqlalchemy"` | 24 |
| `"nplusone.ext.peewee"` | 5 |
| `"NPLUSONE_RAISE"` (setting used by Django and Flask) | 313 |

339 of 399 files, **about 85 %, are Django**. Applied to the downloads, that
is roughly 375k of nplusone's **442,026 downloads in the last 30 days**
([pypistats](https://pypistats.org/packages/nplusone); 554k in October 2026
without mirrors) and about 65k for SQLAlchemy, Flask and Peewee together. The
split assumes CI installs are spread like public repositories, which is not
verifiable. GitHub lists
[2,090 dependent repositories](https://github.com/jmcarp/nplusone/network/dependents)
and 2 packages. libraries.io adds nothing the dependents graph does not
already show.

nplusone's last release is 1.0.0 of 2018-05-21, it has 23 open issues and
pull requests, and on 2026-08-14 a user reported that it crashes on Django 6.1
([nplusone#52](https://github.com/jmcarp/nplusone/issues/52), `ValueError:
too many values to unpack` in `ext/django/patch.py`). Its Django users have to
move somewhere.

### Django test tooling against SQLAlchemy test tooling

Last 30 days on [pypistats](https://pypistats.org/) (the `recent` endpoint,
mirrors excluded):

| Package | Downloads / 30 days | What it says about the market |
|---|---|---|
| django | 41.1M | the framework |
| sqlalchemy | 329.8M | mostly a transitive dependency (pandas, Airflow, MLflow, ...) |
| flask-sqlalchemy | 12.6M | |
| **pytest-django** | **20.8M** | the standard Django test integration; half of Django's downloads |
| pytest-flask-sqlalchemy | 54k | the closest SQLAlchemy equivalent |
| pytest-sqlalchemy | 19k | |
| pytest-alembic | 1.67M | migrations, not queries |
| factory-boy | 20.2M | both ecosystems |

GitHub dependents: pytest-django
[97,855 repositories](https://github.com/pytest-dev/pytest-django/network/dependents).
There is no SQLAlchemy test integration of comparable size: SQLAlchemy users
wire fixtures by hand, which is also why pytest-queryguard listens on the
`Engine` class instead of a fixture.

Query-checking tools only:

| Ecosystem | Tools and downloads / 30 days | Sum |
|---|---|---|
| Django | django-silk 2.29M (a profiler), nplusone Django share ~375k, django-zen-queries 323k, django-zeal 303k (680k in October), django-querycount 218k, django-perf-rec 97k, django-queryinspect 62k, django-auto-prefetch 30k, django-query-counter 20k, django-nplus1 1.7k, django-query-doctor 2.6k | ~1.4M without silk, ~3.7M with it |
| SQLAlchemy | nplusone SQLAlchemy/Flask share ~65k, queryspy 9.5k, pytest-queryguard (663 in the last week; the 30-day figure is not yet computed for a package this new) | ~75k |

Usage of count assertions in public code (GitHub code search):
`assertNumQueries` 57,600 files, `django_assert_num_queries` 5,256,
`django_assert_max_num_queries` 2,064. Django developers already write query
budgets into their tests; they have no file, baseline or report for them.

Surveys agree on a large Django share but not a larger one: the PSF/JetBrains
Python Developers Survey 2022 put SQLAlchemy at 35 % and the Django ORM at 28 %
of respondents ([2022 results](https://lp.jetbrains.com/python-developers-survey-2022/));
the 2024 survey shows Django, FastAPI and Flask each used by about a third
([2024 results](https://lp.jetbrains.com/python-developers-survey-2024/)).
The gap in tooling downloads is larger than the gap in users: Django projects
look for query tools far more than SQLAlchemy projects do.

## 2. Competitors in Django

Downloads are the last 30 days on pypistats; "last release" and open issues
from PyPI and the GitHub API on 2026-11-07.

| Tool | What it detects | Last release | Open issues/PRs | Downloads / 30 d |
|---|---|---|---|---|
| [django-zeal](https://github.com/taobojlen/django-zeal) | N+1 from lazy relations (FK, reverse 1-1, reverse FK, M2M, generic relations), deferred fields (`.only()`/`.defer()`), `.get()` in a loop; threshold, allow-list, unused-ignore check; raises or warns | 2.3.0, 2026-10-14 | 1 | 303k (130k in June, 680k in October) |
| [nplusone](https://github.com/jmcarp/nplusone) (Django ext) | lazy loads, unused eager loads (`select_related`/`prefetch_related` never read) | 1.0.0, 2018-05-21 | 23 | ~375k Django share |
| [django-nplus1](https://github.com/oliverhaas/django-nplus1) | fork of nplusone with zeal's ideas: lazy loads, deferred fields, `.get()` in a loop, duplicate queries (opt-in), unused eager loads and unused fields across the whole pytest session ("corpus mode"), Celery tasks | 0.6.2, 2026-10-04 (first 2026-04-05) | 5 | 1.7k |
| [django-auto-prefetch](https://github.com/adamchainz/django-auto-prefetch) | prevents, does not detect: a base `Model` that prefetches a FK for all peers on first access | 1.14.0, 2025-09-18 | 8 | 30k |
| [django-perf-rec](https://github.com/adamchainz/django-perf-rec) | records the fingerprinted SQL and cache operations of a test to a YAML file and fails when they change | 4.31.0, 2025-09-18 | 6 | 97k |
| [django-querycount](https://github.com/bradmontgomery/django-querycount) | middleware printing query counts and repeated queries per request to the console | 0.8.3, 2023-01-05 | 13 | 218k |
| [django-query-counter](https://github.com/conformist-mw/django-query-counter) | decorator/middleware printing duplicated queries | 0.7.0, 2026-10-02 | 1 | 20k |
| [django-zen-queries](https://github.com/dabapps/django-zen-queries) | `queries_disabled()`: any query in a block raises (for templates, serializers) | 2.1.0, 2021-07-27 | 7 | 323k |
| [django-query-doctor](https://github.com/hassanzaibhay/django-query-doctor) | N+1, duplicates, missing indexes, "fat SELECT", `len(qs)`/`bool(qs)` misuse, complexity, DRF serializer analysis (from its README; not run) | 2.3.0, 2026-08-01 | 0 | 2.6k |
| `assertNumQueries` ([Django docs](https://docs.djangoproject.com/en/stable/topics/testing/tools/#django.test.TransactionTestCase.assertNumQueries)) | exact count on one database alias | Django | | built in |
| `django_assert_num_queries`, `django_assert_max_num_queries` ([pytest-django](https://pytest-django.readthedocs.io/en/latest/helpers.html#django-assert-num-queries)) | exact or maximum count on one connection (`connection=` or `using=`) | pytest-django 4.14.0, 2026-08-10 | 194 (whole project) | 20.8M (whole project) |
| [django-silk](https://github.com/jazzband/django-silk) | profiler: stores each request, its queries and timings in the database, shows them in a UI | 5.6.0, 2026-09-12 | 131 | 2.29M |
| [Scout APM](https://www.scoutapm.com/django-monitoring) | production APM; flags N+1 patterns in traces, "filtered to N+1s consuming a significant amount of time" ([Scout blog](https://scoutapm.com/blog/finding-and-fixing-n-1s-in-django-apps)) | scout-apm 4.1.0, 2026-10-29 | | 162k (all frameworks) |
| Django 6.1 fetch modes ([docs](https://docs.djangoproject.com/en/6.1/topics/db/fetch-modes/)) | prevention: `QuerySet.fetch_mode(FETCH_PEERS)` loads a missing FK, reverse 1-1, deferred field or generic relation for all peers at once; `FETCH_RAISE` raises `FieldFetchBlocked` | Django 6.1, 2026-08-05 | | built in |

Newer tools from 2025 and 2026, found by search and on PyPI, all small:
django-nplus1 and django-query-doctor (above), `django-query-guard` (0.2.1,
2026-08-07, 100 downloads; a pytest plugin for N+1 and query limits),
`django-queryguard-n1` (0.1.0, 2026-09-27, 119 downloads; middleware for API
views), [django-check](https://github.com/richardhapb/django-check) (static
analysis as a language server, "not yet production-ready") and
[orm-lens](https://github.com/FROWNINGdev/orm-lens) (static analysis with an
MCP server). The two `*query-guard*` names are a branding risk for a Django
release of pytest-queryguard; see [Risks](#risks).

### Known weaknesses, from their issue trackers

- **Breakage on new Django versions.** nplusone crashes on Django 6.1
  ([#52](https://github.com/jmcarp/nplusone/issues/52)); django-zeal needed a
  release for 6.1 ([#79](https://github.com/taobojlen/django-zeal/issues/79)),
  because 6.1 moved `GenericForeignKey.__get__` to a private
  `GenericForeignKeyDescriptor` (comment in zeal's `patch.py`). Every tool
  that patches related descriptors pays this on each Django release.
- **False positives.** nplusone: cascade deletes reported as N+1
  ([#47](https://github.com/jmcarp/nplusone/issues/47)), the admin's
  `LogEntry` ([#27](https://github.com/jmcarp/nplusone/issues/27)),
  multi-table inheritance ([#36](https://github.com/jmcarp/nplusone/issues/36)),
  a warning both with and without `prefetch_related`
  ([#34](https://github.com/jmcarp/nplusone/issues/34)). zeal ignores
  singly-loaded instances altogether "because of the high false positive rate"
  (docstring of `patch_global_queryset`), and reported errors outside its
  context ([#19](https://github.com/taobojlen/django-zeal/issues/19)).
  django-nplus1 has an open question on single-result querysets
  ([#23](https://github.com/oliverhaas/django-nplus1/issues/23)) and moved
  unused-eager-load detection to the whole session because per-request
  findings were noisy (its README).
- **Missed cases.** nplusone misses deferred fields
  ([#39](https://github.com/jmcarp/nplusone/issues/39)) and per-instance
  `prefetch_related_objects()` ([#50](https://github.com/jmcarp/nplusone/issues/50));
  zeal had no generic-relation detection until
  [#66](https://github.com/taobojlen/django-zeal/issues/66) (2026-05) and does
  not detect unused prefetches (its README).
- **Side effects of patching.** zeal broke pickling of querysets
  (django-cacheops, [#76](https://github.com/taobojlen/django-zeal/issues/76)),
  left a wrong `_result_cache` after its M2M patch
  ([#59](https://github.com/taobojlen/django-zeal/issues/59), open), and hit
  `RecursionError` with a custom `__eq__`
  ([#28](https://github.com/taobojlen/django-zeal/issues/28)); nplusone has
  two open recursion bugs ([#38](https://github.com/jmcarp/nplusone/issues/38),
  [#44](https://github.com/jmcarp/nplusone/issues/44)).
- **Overhead.** zeal doubled a 5,000-test suite from 5 to 10 minutes
  ([#24](https://github.com/taobojlen/django-zeal/issues/24), fixed later);
  its README now says 3 to 5 %. silk stores every request in the database:
  "Summary page too slow" ([#843](https://github.com/jazzband/django-silk/issues/843)),
  "Any way to run this without saving stuff to DB?"
  ([#773](https://github.com/jazzband/django-silk/issues/773)), memory
  ([#338](https://github.com/jazzband/django-silk/issues/338)).
- **Count assertions.** `assertNumQueries` and pytest-django's fixtures count
  one connection, exactly or as a maximum, and say nothing about which line
  or relation caused the queries; users asked to capture without failing
  ([pytest-django#1034](https://github.com/pytest-dev/pytest-django/issues/1034),
  open since 2022). django-perf-rec records are coupled to test order with
  parametrized tests ([#346](https://github.com/adamchainz/django-perf-rec/issues/346))
  and its fingerprinting is "a lil aggressive"
  ([#285](https://github.com/adamchainz/django-perf-rec/issues/285)).
- **Maintenance.** nplusone, django-querycount (2023) and django-zen-queries
  (2021) have had no release for years; django-zeal is actively maintained by
  one author and is the tool to beat. django-nplus1 needs Python 3.14 or newer
  (`requires_python >=3.14` on PyPI), which limits it to few projects today.

### Where pytest-queryguard could be clearly best

Same lens as the SQLAlchemy [comparison](comparison.md):

- **Precision.** Group lazy loads by the line of the query that loaded the
  parents and count distinct parents, as for SQLAlchemy, instead of "the same
  query twice" (zeal's threshold of 2 per model, field and caller). Recognize
  pagination, chunked `IN` lists (Django's prefetch batches) and catalog
  reads, which the repeat rule already does on the statement text. Exclude
  cascade deletes as one unit of work (the nplusone#47 class of reports).
- **Recall.** Cover what each tool misses: deferred fields (nplusone), unused
  prefetches (zeal), Core-level repeats and write loops (`save()` in a loop),
  which no Django N+1 tool reports; zeal and nplusone only see relation
  accesses.
- **Overhead.** Publish measurements as for SQLAlchemy (+1.8 to 2.4 % on the
  300-test suite, [Performance](performance.md)); zeal claims 3 to 5 % and
  once doubled a suite.
- **Features nobody in Django has:** a baseline file for adopting on an old
  code base (zeal and django-nplus1 have allow-lists in settings), SARIF for
  code scanning, a budget file written from a run (django-perf-rec is the
  nearest, with whole-SQL snapshots that churn), `--queryguard-json` with
  the PR comparison and the GitHub Action, an EXPLAIN-based missing-index check
  on SQLite, PostgreSQL and MySQL with hypopg verification (django-query-doctor
  claims missing indexes; not verified how), patches that add
  `select_related()`/`prefetch_related()`, and an MCP server.
- **Not best, and not worth chasing first:** production profiling (silk,
  Scout), prevention (Django 6.1 `FETCH_PEERS`, django-auto-prefetch), and
  Celery coverage (django-nplus1).

## 3. Technical design

### Where Django lets us observe what we need

**Statements: `connection.execute_wrapper`**
([Database instrumentation](https://docs.djangoproject.com/en/stable/topics/db/instrumentation/),
public since Django 2.0). A wrapper `(execute, sql, params, many, context)`
runs around every statement of one connection, `executemany` included
(`many=True`), with `context["connection"]` and `context["cursor"]`. It is per
connection object, and Django's connections are per thread, so the plugin
installs it on each connection as it is created (`connection_created`
signal) and on those already open (`connections.all()`). It also sees the
`SAVEPOINT`/`RELEASE` statements of `atomic()` (they go through the same
cursor wrapper; `assertNumQueries` counts them too), which the rules must
skip and the budget must document. This one hook gives `StatementEvent`
(text, parameters, `executemany`, duration, call site) and so the repeat,
small-repeat, write-loop and budget rules, `record_queries()`, the fixture,
and the statements for the missing-index check. Django passes parameters as a
list with `%s` placeholders and renders `LIMIT`/`OFFSET` as literals; the
detectors already read both (`detectors._BIND`, `_PAGE_LITERAL`).

**Lazy relation loads (relationship-load).**

- Django 6.1 has one choke point for single-valued fetches:
  `instance._state.fetch_mode.fetch(fetcher, instance)` is called by
  `ForwardManyToOneDescriptor.__get__` (FK and forward 1-1),
  `ReverseOneToOneDescriptor.__get__`, `DeferredAttribute.__get__`
  (`query_utils.py`) and the generic foreign key
  (`contrib/contenttypes/fields.py`) when the value is not cached; read in
  `django/db/models/fetch_modes.py` on `stable/6.1.x`. Wrapping
  `FetchOne.fetch` and `FetchPeers.fetch` (two classes, three lines each)
  sees the field (`fetcher.field`), the instance and, through
  `instance._state.peers`, the instances of the same queryset. The classes are
  not documented as extensible, so this is a private hook, but a far smaller
  one than the descriptors.
- Django 5.2 LTS (supported until April 2028, see
  [supported versions](https://www.djangoproject.com/download/#supported-versions))
  and 6.0 have no fetch modes:
  patch the descriptors as zeal does (`ForwardManyToOneDescriptor.get_queryset`,
  `ReverseOneToOneDescriptor.get_queryset`, `DeferredAttribute._check_parent_chain`,
  the generic foreign key's `__get__`).
- Collections (reverse FK and M2M managers) are outside fetch modes in every
  version ("fetch modes don't affect such managers' queries", fetch-modes
  docs). A lazy collection load is a manager's queryset evaluated without a
  prefetch cache hit: wrap the manager classes made by
  `create_reverse_many_to_one_manager` and
  `create_forward_many_to_many_manager` (zeal patches these factory functions)
  and mark the querysets they return, then report when such a queryset runs
  its `_fetch_all`. `.all()` with a prefetched cache does not query and is not
  reported; `.filter()` on a prefetched relation queries again and is
  reported, which is the correct finding.
- Per-instance `prefetch_related_objects([obj], ...)` in a loop is an N+1
  that zeal fixed in 2026 (#57) and nplusone misses (#50): wrap
  `get_prefetch_querysets` as zeal does, or simply let the repeat rule see it.
- **Parent attribution**, which the SQLAlchemy rule and the fix hint rely on
  (`ParentLoad`): note the call site of the queryset that created each
  instance when `ModelIterable` builds it (`Model.from_db` is the documented
  override point), and the relation chain for instances reached through
  `select_related`. The rule then groups lazy loads by (relation, parent query
  line) and counts distinct `pk`s, exactly as `detectors._relationship_findings`
  does today on `RelationshipLoadEvent`. Single-row parents (`.get()`,
  `.first()`) come from different lines, so they do not form a group; that
  answers zeal's "singly-loaded" problem without ignoring them.

**Deferred fields (column-load).** A read of a field left out by `.only()` or
`.defer()` goes through `DeferredAttribute.__get__` and then
`refresh_from_db(fields=[name])`, one query per instance. On 6.1 it passes the
fetch-mode hook above; on 5.2/6.0 patch `DeferredAttribute` (zeal patches
`_check_parent_chain`). That is `CAUSE_DEFERRED`; an explicit
`refresh_from_db()` is `CAUSE_REFRESH`. Django has no expire-on-commit, so
`CAUSE_EXPIRED` does not occur. Multi-table inheritance (a child reached from
its parent) is a reverse one-to-one load, so it is a relationship-load.

**Write loops.** `save()`, `delete()` and `update()` in a loop are single-row
statements from one line: the existing rule on `StatementEvent`. The
SQLAlchemy rule excuses statements of one flush; the Django equivalent is
one `Collector.delete()` (cascades), one `bulk_create`/`bulk_update` (which
may batch) and one `QuerySet.delete()`. Give the statements inside these
calls the same `flush` number by wrapping `django.db.models.deletion.Collector.delete`.

**Unused eager loads.** Which relations were eager-loaded: read
`queryset.query.select_related` (a dict, or `True` for all) and
`queryset._prefetch_related_lookups` when `_fetch_all` runs. Which were read:
a cached forward FK still passes `ForwardManyToOneDescriptor.__get__`, and a
prefetched collection passes the manager's `get_queryset()`, so both are
observable with class-level wrappers that run only while the check is on.
nplusone did this for Django; django-nplus1 found the per-request version
noisy and switched to whole-session aggregation.

**Unused columns.** Concrete fields are read from `instance.__dict__`
because `DeferredAttribute` is a non-data descriptor: no hook. django-nplus1's
trick (`fields.py`) is to give `DeferredAttribute` a `__set__` while the check
is on, which makes it a data descriptor so every read runs `__get__`. That is
the counterpart of our `readhook` (`InstrumentedAttribute.__get__`) and
costs a Python call per field read, so it stays experimental and off by
default, as for SQLAlchemy.

**Cartesian loads.** `select_related` follows only single-valued relations
and `prefetch_related` runs separate queries, so the SQLAlchemy cartesian
case (two joined eager collections) does not exist in Django. The Django
counterpart is two multi-valued joins in one query from filters or
aggregates: `annotate(Count("orders"), Count("notes"))` gives wrong counts,
a pitfall the Django docs describe under
[combining multiple aggregations](https://docs.djangoproject.com/en/stable/topics/db/aggregation/#combining-multiple-aggregations).
It is detectable from `query.alias_map` at compile time; a candidate for a
later release, not the first.

**Missing indexes.** The check needs the statement, its parameters and a
cursor in the same transaction. `context["connection"]` gives a cursor; the
plan readers (`indexes`, `sqlite_index`, `mysql_index`, `predicates`,
`partial`, `advisor`, `hypopg`) work on plan text and DB-API cursors. What
changes is the driver glue: psycopg 2 and 3 with `%s` (no asyncpg), sqlite3
(Django converts `%s` to `?` in its own cursor wrapper), mysqlclient. Under
pytest-django every test runs in a transaction (`TestCase`) and database
access is blocked outside `django_db` tests; the EXPLAIN runs inside the
wrapper, so it is inside the allowed access.

**Call sites.** `callsite.py` skips frames of internal packages
(`_INTERNAL_PACKAGES = ("sqlalchemy", "pytest", ...)`). Django needs
`django`, `asgiref` and, as a library, `rest_framework`. In Django REST
framework the parent query runs inside `ListModelMixin.list`, and the user's
line is a class attribute (`queryset = Book.objects.all()`) that is not a
frame: the finding falls back to the serializer line, and the fix hint must
point at the view's `queryset` or `get_queryset()`. DRF is where most Django
N+1s live, so this needs its own scenarios.

**Fix patches.** Simpler than for SQLAlchemy: the fix is a method on the
queryset (`.select_related("author")` for a single-valued relation,
`.prefetch_related("books")` for a collection), with no import to add. Insert
it after `Model.objects` on a certain line, with the same "every doubt means
no patch" rules as `fixpatch.py`. Custom managers whose `get_queryset()`
returns something unusual are the main doubt.

**How django-zeal does it** (read in `src/zeal/patch.py`, 695 lines, and
`listeners.py`): it patches at `AppConfig.ready()`: the descriptor
`get_queryset`s, the manager factory functions, `DeferredAttribute._check_parent_chain`,
the generic foreign key, `QuerySet._fetch_all`, `QuerySet.get`,
`QuerySet._clone`, `Model.refresh_from_db`, `QuerySet.__getstate__` (for the
pickling bug) and `prefetch_related_objects`. Each notification is keyed by
(model, field, caller) in a `ContextVar`, and an N+1 is reported when the
count reaches the threshold (2 by default). It sees relation accesses only,
no statements, so it has no repeat, write-loop, budget or index rules.

### What is reusable today

Read from `src/pytest_queryguard` at `02cd220` (0.8.0 plus stabilization).
"Imports SQLAlchemy" means the module or its `_compat` import names it.

ORM-agnostic, usable for Django as they are (they work on `StatementEvent`,
`RelationshipLoadEvent`, `ColumnLoadEvent`, `ScanEvent` and `Finding`):

- data model and rules: `events.py`, `findings.py`, `collector.py`,
  `detectors.py` (relationship, repeat, small-repeat, column-load, write-loop
  rules; SQLAlchemy appears only in comments), `blocks.py` (`ignore()`),
  `ignores.py`, `groups.py`, `warnings.py`;
- files and reports: `baseline.py`, `budgets.py`, `testjson.py`, `sarif.py`,
  `compare.py`, `trend.py`, `fileio.py`, `messages.py` (text; some fix wording
  names SQLAlchemy loaders and would need an ORM-specific variant);
- index check: `indexes.py`, `sqlite_index.py`, `mysql_index.py`,
  `predicates.py`, `partial.py`, `advisor.py`, `hypopg.py`, `catalog.py`,
  `sqltext.py`, `tablenames.py` (they read plans and catalogs through DB-API
  cursors; the asyncpg and SQLAlchemy-connection glue in `indexes.py` and
  `catalog.py` is the part to split);
- user-facing: `api.py` (`max_queries`, `max_db_time`, `record_queries`,
  `QueryLog`, `Query`, the fixture; it calls `listeners.install()`, which is
  the one place to make pluggable), `plugin.py` (options, markers, report,
  baseline/SARIF/JSON wiring; it imports `listeners` and `callsite`), `mcp.py`;
- in-app mode: `app/_core.py`, `app/_wsgi.py`, `app/_asgi.py`, `app/_page.py`,
  `app/_panel.py`, `app/_reporters.py` (the WSGI middleware already runs in
  Django, but sees only SQLAlchemy statements).

Tied to SQLAlchemy (an integration would have Django counterparts):
`listeners.py` (1,182 lines: engine, session, mapper and attribute events),
`_compat.py` (204, private SQLAlchemy names), `callsite.py` (partly: internal
packages, `Session` frames, greenlets), `eager.py` (345), `unused_columns.py`
(453), `cartesian.py` (417), `readhook.py` (83), `fixpatch.py` (1,153),
`lint.py` (1,265, the static check), `app/_listeners.py` (271).

That is about 5,500 of 21,700 source lines (25 %) tied to SQLAlchemy; the
rest, including the rules and every file format, is shared.

### What to change before 1.0

Nothing in the public API is SQLAlchemy-specific in its names or signatures:
the finding kinds (`relationship-load`, `column-load`, `unused-eager-load`, ...),
the identity `(kind, label, file, function)`, the SARIF rule ids, the file
formats, the markers, the fixture and `__all__` all fit Django. A
relationship label `Book.author` has the same shape for a Django model and
field. So Django can be added in 1.x without a breaking change. Two small
changes before 1.0 keep it that way:

1. **Make the SQLAlchemy dependency movable.** Today `dependencies =
   ["pytest>=8", "sqlalchemy>=2.0.13"]`, and the plugin imports SQLAlchemy at
   load (`plugin.py` imports `listeners`). A Django project should not need
   SQLAlchemy, but dropping a required dependency in 1.x would break an
   environment that relied on it arriving transitively. Before 1.0: add an
   extra `pytest-queryguard[sqlalchemy]` (same requirement, so nothing
   changes today), document it as the way to install for SQLAlchemy, and say
   in [stability](stability.md) that the core dependency may move into that
   extra in a minor release. In 1.x, add `[django]` and make the plugin import
   each integration only when its ORM is installed.
2. **Word the contract ORM-neutrally where it names SQLAlchemy.** The public
   texts that define behaviour say "SQLAlchemy" in places where a Django
   meaning will be needed: `queryguard_scope = session` ("each SQLAlchemy
   Session or connection checkout"; for Django this would be each request
   through the test client, or each connection), `Query.sql` ("as SQLAlchemy
   sent it"), `Query.relationship`, `record_queries()` ("every SQL statement
   SQLAlchemy sends"), and "Supported versions: Python, SQLAlchemy and pytest"
   in stability.md. Rewording them as "the ORM's unit of work (for SQLAlchemy,
   the Session ...)" now costs nothing; changing a documented meaning after
   1.0 is what the policy forbids.

Not needed before 1.0, because they are internal: a backend interface
between `api.install()` and the listeners, lazy imports in `plugin.py`, a
split of `callsite.py` and of the driver glue in `indexes.py`.

### Effort and risks

Estimate, measured against this repository (first commit 2026-10-11, about
1,500 commits and 48k lines of tests to 0.8.0):

| Part | New code (lines, rough) | Notes |
|---|---|---|
| Backend seam, lazy imports, `[django]` extra | 300 | internal refactor of `api`/`plugin`/`callsite` |
| `execute_wrapper` statements, call sites, savepoint handling | 400 | unlocks repeat, write-loop, budgets, JSON, SARIF, baseline |
| Relation loads: fetch-mode hook (6.1+), descriptor patches (5.2, 6.0), managers, parent attribution | 900 | the core of the Django release |
| Deferred-field loads, cascade/bulk grouping | 300 | |
| Index check driver glue (psycopg 2/3, sqlite3, mysqlclient) | 300 | plan readers unchanged |
| Django test project, CI matrix (5.2, 6.0, 6.1, main × SQLite, PostgreSQL, MySQL), accuracy scenarios (incl. DRF), real-world runs | 4,000+ in tests | the larger part of the work |
| Later: unused eager loads, unused columns, fix patches, aggregate cartesian, in-app | 2,500 | after the first Django release |

At the pace of 0.1 to 0.8, a first Django release is roughly **4 to 6 weeks**
of work and parity with the SQLAlchemy feature set another **6 to 8 weeks**.
Both are estimates, not measurements.

#### Risks

- **Private Django internals.** Relation hooks rely on private names
  (`_state.fetch_mode`, `FetchOne`, `_check_parent_chain`, manager factories,
  `GenericForeignKeyDescriptor`). nplusone died of this (#52) and zeal needs a
  release per Django version (#79). Mitigation: a `_django_compat.py` like
  `_compat.py`, the 6.1 fetch-mode hook as the main path, and CI against
  Django's main branch.
- **Side effects of patching.** zeal's pickling (#76), `_result_cache` (#59)
  and recursion (#28) bugs. Mitigation: patch classes, not querysets; attach
  no closures to instances; install only while a test body is measured.
- **False positives** in Django-specific places: the admin, cascade deletes,
  multi-table inheritance, factories in loops (factory-boy is used by both
  ecosystems; the plugin measures the test body, not fixtures), DRF.
  Mitigation: the accuracy corpus and real-world runs before the release, as
  for SQLAlchemy; candidates with pytest suites to check: Saleor, django-oscar,
  Django REST framework's own tests.
- **Prevention in Django itself.** `FETCH_PEERS` and `FETCH_RAISE` (6.1)
  remove some N+1s at the source and let a strict test fail without any
  plugin. Detection still matters for 5.2 LTS projects, collections (not
  covered by fetch modes), repeats, write loops, budgets and indexes, but the
  N+1 message must say when `fetch_mode` is the simpler fix.
- **A strong incumbent.** django-zeal is maintained, fast-growing (130k to
  680k downloads a month from June to October 2026) and good at its narrow job.
  pytest-queryguard wins on breadth and CI features, not on "detects N+1".
- **Name confusion.** `django-query-guard` and `django-queryguard-n1` exist on
  PyPI (both 2026, around 100 downloads a month). Docs and the README should
  say plainly that pytest-queryguard is not related.
- **Maintenance load.** A second ORM doubles the version matrix and the
  surface for regressions, for one maintainer. The stabilization rule (no new
  features until stable) applies to the Django part on its own: ship it
  marked experimental for at least one minor release.

## 4. Recommendation

**Go**, after 1.0.0 is out and stable (no open false-positive reports for a
few weeks). Not before: the 1.0 freeze and the SQLAlchemy real-world
validation come first.

Before 1.0: the two changes in [What to change before 1.0](#what-to-change-before-10)
(the `[sqlalchemy]` extra with a note in the stability policy, and
ORM-neutral wording of `scope = session`, `Query`, `record_queries()` and the
supported-versions line). Nothing else.

First Django release (a 1.x minor, experimental for at least one minor):

- Django 5.2 LTS, 6.0 and 6.1 (6.2 when it ships), Python as the package;
  SQLite, PostgreSQL (psycopg 2 and 3) and MySQL;
- installed with `pytest-queryguard[django]`, working alongside pytest-django
  with no settings change (no `INSTALLED_APPS` or middleware entry, which is
  what zeal and nplusone require);
- finding kinds: `relationship-load` (FK, 1-1 both ways, reverse FK and M2M
  managers, generic relations), `column-load` (deferred fields),
  `repeated-statement`, `write-loop`, `missing-index`;
- everything that works from those kinds today: markers, the fixture,
  `record_queries()`, budgets and the budget file, baseline, `--queryguard-json`,
  compare and the GitHub Action, SARIF, strict mode, `ignore()`;
- the fix hint names `select_related`/`prefetch_related` (and `fetch_mode` on
  6.1), without automatic patches yet;
- published accuracy and overhead numbers against django-zeal, on the same
  scenarios.

Later: `unused-eager-load`, `unused-columns`, fix patches, the
multiple-aggregation join check, Celery and the in-app mode for Django, and
the static check.

## Sources

Downloads: [pypistats.org](https://pypistats.org/) API (`/api/packages/<name>/recent`
and `/overall?mirrors=false`), read 2026-11-07. Releases: PyPI JSON API.
Repositories, issues and dependents: GitHub (API and the
`network/dependents` pages), 2026-11-07. Code search: GitHub REST
`search/code`, 2026-11-07. Django internals: `django/django` branch
`stable/6.1.x`, files `db/models/fetch_modes.py`, `db/models/base.py`,
`db/models/fields/related_descriptors.py`, `db/models/query_utils.py`,
`contrib/contenttypes/fields.py`, `db/backends/base/base.py`,
`db/backends/utils.py`. Competitor internals: `taobojlen/django-zeal`
(`src/zeal/patch.py`, `listeners.py`, `apps.py`, README),
`oliverhaas/django-nplus1` (README, `django_nplus1/fields.py`),
`pytest-dev/pytest-django` (`pytest_django/fixtures.py`), default branches on
2026-11-07.
