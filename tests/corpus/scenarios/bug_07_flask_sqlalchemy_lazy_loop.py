"""BUG relationship-load: a Flask-SQLAlchemy view reads ``author.books`` per author.

Flask-SQLAlchemy models and ``db.session``; the view builds a dict per author
and counts the lazy ``books`` collection of each one.
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})
REQUIRES = ("flask_sqlalchemy",)


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Author(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    books: Mapped[list["Book"]] = relationship(back_populates="author")


class Book(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped[Author] = relationship(back_populates="books")


def make_app(ctx: Ctx) -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = ctx.url
    db.init_app(app)
    return app


def setup(ctx: Ctx) -> None:
    ctx.reset()
    with make_app(ctx).app_context():
        db.create_all()
        for a in range(1, 9):
            db.session.add(
                Author(id=a, name=f"a{a}", books=[Book(title=f"b{a}{k}") for k in range(3)])
            )
        db.session.commit()
        db.engine.dispose()


def run(ctx: Ctx) -> None:
    with make_app(ctx).app_context():
        authors = db.session.scalars(db.select(Author)).all()
        rows = [{"name": a.name, "books": len(a.books)} for a in authors]
        db.engine.dispose()
    assert len(rows) == 8
