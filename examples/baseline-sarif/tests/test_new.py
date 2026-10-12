from app.books import titles_of
from app.models import Author


def test_titles_of(session):
    assert titles_of(session, session.get(Author, 1)) == ["book 1"]
