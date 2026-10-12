"""A small shop: customers, orders, order lines, products, tags, categories.

Every relationship uses SQLAlchemy's default loading (lazy ``select``), so
code that walks them without an eager option emits a query per object.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
)

from ..kit import Ctx


class Base(AsyncAttrs, DeclarativeBase):
    pass


product_tag = Table(
    "product_tag",
    Base.metadata,
    Column("product_id", ForeignKey("product.id"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id"), primary_key=True),
)


class Category(Base):
    __tablename__ = "category"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    children: Mapped[list[Category]] = relationship(back_populates="parent")
    parent: Mapped[Category | None] = relationship(
        back_populates="children", remote_side=[id]
    )
    products: Mapped[list[Product]] = relationship(back_populates="category")


class Tag(Base):
    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))


class Product(Base):
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    price: Mapped[int] = mapped_column(default=100)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), index=True
    )
    category: Mapped[Category | None] = relationship(back_populates="products")
    tags: Mapped[list[Tag]] = relationship(secondary=product_tag)


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(80))
    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "order"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="new")
    total: Mapped[int] = mapped_column(default=0)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    lines: Mapped[list[OrderLine]] = relationship(back_populates="order")


class OrderLine(Base):
    __tablename__ = "order_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    qty: Mapped[int] = mapped_column(default=1)
    order: Mapped[Order] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


def seed(
    session: Session,
    *,
    customers: int = 8,
    orders: int = 3,
    lines: int = 2,
    products: int = 12,
    tags: int = 4,
) -> None:
    """Deterministic rows: ids start at 1 and are dense."""
    tag_rows = [Tag(id=i, name=f"tag{i}") for i in range(1, tags + 1)]
    cats = [Category(id=i, name=f"cat{i}") for i in range(1, 4)]
    prods = [
        Product(
            id=i,
            name=f"product{i}",
            price=10 * i,
            category=cats[i % 3],
            tags=tag_rows[: 1 + i % tags],
        )
        for i in range(1, products + 1)
    ]
    session.add_all([*tag_rows, *cats, *prods])
    order_id = line_id = 0
    for c in range(1, customers + 1):
        customer = Customer(id=c, name=f"customer{c}", email=f"c{c}@example.com")
        session.add(customer)
        for _ in range(orders):
            order_id += 1
            order = Order(id=order_id, customer=customer, total=order_id * 5)
            session.add(order)
            for k in range(lines):
                line_id += 1
                session.add(
                    OrderLine(
                        id=line_id,
                        order=order,
                        product=prods[(line_id + k) % products],
                        qty=1 + k,
                    )
                )
    session.commit()


def setup(ctx: Ctx, **seed_kwargs: int) -> None:
    """The shared ``setup`` of the scenarios that only need the shop rows."""
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        seed(session, **seed_kwargs)
