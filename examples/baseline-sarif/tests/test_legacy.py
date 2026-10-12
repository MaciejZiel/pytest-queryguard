from app.books import books_per_author, titles_with_authors


def test_titles_with_authors(session):
    assert len(titles_with_authors(session)) == 8


def test_books_per_author(session):
    assert len(books_per_author(session)) == 8
