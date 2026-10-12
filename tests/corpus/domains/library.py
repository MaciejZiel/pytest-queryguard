"""A small library: publishers, authors, books, reviews.

It carries the behaviour that hides queries in ordinary-looking code: a
``__str__`` that reads a relationship, a property that counts a collection, a
hybrid property with a Python side, a deferred column and a dynamic
relationship.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, ForeignKey, String, Text, exists
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    deferred,
    mapped_column,
    relationship,
)

from ..kit import Ctx


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "author"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    books: Mapped[list[Book]] = relationship(back_populates="author")
    books_dyn = relationship("Book", lazy="dynamic", viewonly=True)

    @hybrid_property
    def has_books(self) -> bool:
        return len(self.books) > 0

    @has_books.inplace.expression
    @classmethod
    def _has_books_expression(cls) -> ColumnElement[bool]:
        return exists().where(Book.author_id == cls.id)


class Book(Base):
    __tablename__ = "book"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(60))
    pages: Mapped[int] = mapped_column(default=200)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"), index=True)
    synopsis: Mapped[str] = deferred(mapped_column(Text, default="synopsis"))
    author: Mapped[Author] = relationship(back_populates="books")
    reviews: Mapped[list[Review]] = relationship(back_populates="book")

    @property
    def review_count(self) -> int:
        return len(self.reviews)

    @hybrid_property
    def is_long(self) -> bool:
        return self.pages > 300

    def __str__(self) -> str:
        return f"{self.title} by {self.author.name}"


class Review(Base):
    __tablename__ = "review"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    stars: Mapped[int] = mapped_column(default=5)
    book: Mapped[Book] = relationship(back_populates="reviews")


def seed(
    session: Session, *, authors: int = 6, books: int = 3, reviews: int = 2
) -> None:
    book_id = review_id = 0
    for a in range(1, authors + 1):
        author = Author(id=a, name=f"author{a}")
        session.add(author)
        for _ in range(books):
            book_id += 1
            book = Book(
                id=book_id,
                title=f"book{book_id}",
                pages=100 + 50 * book_id,
                author=author,
            )
            session.add(book)
            for _ in range(reviews):
                review_id += 1
                session.add(Review(id=review_id, book=book, stars=1 + review_id % 5))
    session.commit()


def setup(ctx: Ctx, **seed_kwargs: int) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        seed(session, **seed_kwargs)
