from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    WriteOnlyMapped,
    deferred,
    mapped_column,
    relationship,
)


class Base(AsyncAttrs, DeclarativeBase):
    pass


post_tag = Table(
    "post_tag",
    Base.metadata,
    Column("post_id", ForeignKey("post.id"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id"), primary_key=True),
)


class Parent(Base):
    __tablename__ = "parent"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    children: Mapped[list[Child]] = relationship(back_populates="parent")
    kids_dyn = relationship("Child", lazy="dynamic", viewonly=True)
    kids_wo: WriteOnlyMapped[Child] = relationship("Child", viewonly=True)


class Child(Base):
    __tablename__ = "child"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    blob: Mapped[str] = deferred(mapped_column(String(50)))
    parent: Mapped[Parent] = relationship(back_populates="children")


class Post(Base):
    __tablename__ = "post"
    id: Mapped[int] = mapped_column(primary_key=True)
    tags: Mapped[list[Tag]] = relationship(secondary=post_tag)


class Tag(Base):
    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))


class Node(Base):
    __tablename__ = "node"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("node.id"))
    parent: Mapped[Node | None] = relationship(remote_side=[id], back_populates="kids")
    kids: Mapped[list[Node]] = relationship(back_populates="parent")


class Log(Base):
    __tablename__ = "log"
    id: Mapped[int] = mapped_column(primary_key=True)
    msg: Mapped[str] = mapped_column(String(20))
