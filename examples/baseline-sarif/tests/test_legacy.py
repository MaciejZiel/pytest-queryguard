from app.books import authors_by_id, titles_with_authors


def test_titles_with_authors(session):
    assert len(titles_with_authors(session)) == 8


def test_authors_by_id(session):
    assert len(authors_by_id(session, list(range(1, 9)))) == 8
