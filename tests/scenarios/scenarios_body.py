import asyncio

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import (
    Session,
    joinedload,
    raiseload,
    selectinload,
    subqueryload,
    undefer,
)

from models import Child, Log, Node, Parent, Post, Tag


def sess(db):
    return Session(db[0])


def test_01_lazy_n1_bad(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent)):
            _ = [c.name for c in p.children]


def test_02a_selectinload_ok(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent).options(selectinload(Parent.children))):
            _ = [c.name for c in p.children]


def test_02b_joinedload_ok(db):
    with sess(db) as s:
        for p in s.scalars(
            select(Parent).options(joinedload(Parent.children))
        ).unique():
            _ = [c.name for c in p.children]


def test_02c_subqueryload_ok(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent).options(subqueryload(Parent.children))):
            _ = [c.name for c in p.children]


def test_03_session_get_loop_bad(db):
    with sess(db) as s:
        for i in range(1, 7):
            s.get(Parent, i)


def test_03b_session_get_loop_3_bad(db):
    with sess(db) as s:
        for i in range(1, 4):
            s.get(Parent, i)


def test_04_async_awaitable_attrs_bad(db):
    async def main():
        eng = create_async_engine(db[1].replace("sqlite:", "sqlite+aiosqlite:"))
        async with AsyncSession(eng) as s:
            for p in (await s.scalars(select(Parent))).all():
                _ = [c.name for c in await p.awaitable_attrs.children]
        await eng.dispose()

    asyncio.run(main())


def test_04b_async_selectin_ok(db):
    async def main():
        eng = create_async_engine(db[1].replace("sqlite:", "sqlite+aiosqlite:"))
        async with AsyncSession(eng) as s:
            for p in (
                await s.scalars(select(Parent).options(selectinload(Parent.children)))
            ).all():
                _ = [c.name for c in p.children]
        await eng.dispose()

    asyncio.run(main())


def test_04c_async_get_loop_bad(db):
    async def main():
        eng = create_async_engine(db[1].replace("sqlite:", "sqlite+aiosqlite:"))
        async with AsyncSession(eng) as s:
            for i in range(1, 7):
                await s.get(Parent, i)
        await eng.dispose()

    asyncio.run(main())


def _rep(db, n):
    with sess(db) as s:
        for i in range(1, n + 1):
            s.scalars(select(Child).where(Child.parent_id == i)).all()


def test_05a_repeat_x2(db):
    _rep(db, 2)


def test_05b_repeat_x3_batch_legit(db):
    _rep(db, 3)


def test_05c_repeat_x4(db):
    _rep(db, 4)


def test_05d_repeat_x5(db):
    _rep(db, 5)


def test_05e_repeat_x6(db):
    _rep(db, 6)


def test_06_legit_diff_shapes_ok(db):
    with sess(db) as s:
        s.scalars(select(Parent)).all()
        s.scalars(select(Child)).all()
        s.scalars(select(Tag)).all()
        s.scalars(select(Post)).all()
        s.scalars(select(Node)).all()
        s.scalars(select(Log)).all()


def _three(db):
    with sess(db) as s:
        s.scalars(select(Parent)).all()
        s.scalars(select(Child)).all()
        s.scalars(select(Tag)).all()


def _four(db):
    _three(db)
    with sess(db) as s:
        s.scalars(select(Post)).all()


@pytest.mark.max_queries(3)
def test_07a_budget_exact_ok(db):
    _three(db)


@pytest.mark.max_queries(3)
def test_07b_budget_over_by_1_bad(db):
    _four(db)


def test_08_m2m_lazy_bad(db):
    with sess(db) as s:
        for p in s.scalars(select(Post)):
            _ = [t.name for t in p.tags]


def test_08b_m2m_selectin_ok(db):
    with sess(db) as s:
        for p in s.scalars(select(Post).options(selectinload(Post.tags))):
            _ = [t.name for t in p.tags]


def test_09_selfref_parent_walk_bad(db):
    with sess(db) as s:
        for n in s.scalars(select(Node).where(Node.parent_id.is_not(None))):
            _ = n.parent.id


def test_09b_selfref_joined_ok(db):
    with sess(db) as s:
        for n in s.scalars(
            select(Node)
            .where(Node.parent_id.is_not(None))
            .options(joinedload(Node.parent))
        ):
            _ = n.parent.id


def test_09d_many_to_one_lazy_bad(db):
    with sess(db) as s:
        for c in s.scalars(select(Child)):
            _ = c.parent.name


def test_09e_many_to_one_joinedload_ok(db):
    with sess(db) as s:
        for c in s.scalars(select(Child).options(joinedload(Child.parent))):
            _ = c.parent.name


def test_09c_selfref_kids_lazy_bad(db):
    with sess(db) as s:
        for n in s.scalars(select(Node)):
            _ = n.kids


def test_10a_dynamic_loop_bad(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent)):
            _ = p.kids_dyn.all()


def test_10b_writeonly_loop_bad(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent)):
            _ = s.scalars(p.kids_wo.select()).all()


def test_10c_raiseload_ok(db):
    with sess(db) as s:
        for p in s.scalars(select(Parent).options(raiseload(Parent.children))):
            with pytest.raises(InvalidRequestError):
                _ = p.children


def test_11a_deferred_loop_bad(db):
    with sess(db) as s:
        for c in s.scalars(select(Child)):
            _ = c.blob


def test_11b_undefer_ok(db):
    with sess(db) as s:
        for c in s.scalars(select(Child).options(undefer(Child.blob))):
            _ = c.blob


def test_11c_expire_after_commit_refresh_bad(db):
    with sess(db) as s:
        ps = s.scalars(select(Parent)).all()
        s.commit()
        for p in ps:
            _ = p.name


def test_11d_expire_after_commit_few_ok(db):
    with sess(db) as s:
        ps = s.scalars(select(Parent)).all()[:3]
        s.commit()
        for p in ps:
            _ = p.name


def test_11e_expire_on_commit_false_ok(db):
    with Session(db[0], expire_on_commit=False) as s:
        ps = s.scalars(select(Parent)).all()
        s.commit()
        for p in ps:
            _ = p.name


def test_12a_insert_flush_per_row(db):
    with sess(db) as s:
        for i in range(6):
            s.add(Log(msg=f"m{i}"))
            s.flush()


def test_12b_add_all_one_flush_ok(db):
    with sess(db) as s:
        s.add_all([Log(msg=f"m{i}") for i in range(6)])
        s.flush()


def test_12c_core_insert_per_row(db):
    with sess(db) as s:
        for i in range(6):
            s.execute(insert(Log).values(msg=f"m{i}"))


def test_12d_core_executemany_ok(db):
    with sess(db) as s:
        s.execute(insert(Log), [{"msg": f"m{i}"} for i in range(6)])


@pytest.mark.max_queries(2)
def test_12e_budget_counts_writes_bad(db):
    with sess(db) as s:
        for i in range(4):
            s.add(Log(msg=f"m{i}"))
            s.flush()


def test_13a_core_select_loop_bad(db):
    with db[0].connect() as c:
        for i in range(1, 7):
            c.execute(select(Child.id).where(Child.parent_id == i)).all()


def test_13b_count_loop_bad(db):
    with sess(db) as s:
        for i in range(1, 7):
            s.scalar(
                select(func.count()).select_from(Child).where(Child.parent_id == i)
            )


def test_14a_pagination_3_pages_legit(db):
    with sess(db) as s:
        for page in range(3):
            s.scalars(select(Child).order_by(Child.id).limit(5).offset(page * 5)).all()


def test_14b_two_parents_legit(db):
    with sess(db) as s:
        a, b = s.get(Parent, 1), s.get(Parent, 2)
        _ = a.children, b.children


def test_14c_same_parent_twice_ok(db):
    with sess(db) as s:
        a = s.get(Parent, 1)
        for _ in range(6):
            _ = a.children


def test_14d_identity_map_get_ok(db):
    with sess(db) as s:
        keep = s.get(Parent, 1)  # a reference keeps it in the identity map
        for _ in range(6):
            s.get(Parent, 1)
        assert keep is not None


def test_14e_in_list_varying_len_legit(db):
    with sess(db) as s:
        for n in range(1, 7):
            s.scalars(select(Parent).where(Parent.id.in_(range(1, n + 1)))).all()


def test_14f_literal_binds_loop_bad(db):
    with sess(db) as s:
        for i in range(1, 7):
            s.execute(text(f"SELECT * FROM child WHERE parent_id = {i}")).all()
