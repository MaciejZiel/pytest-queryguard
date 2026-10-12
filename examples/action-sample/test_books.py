from models import Book, make_session


def test_author_names_n_plus_one():
    # The lazy load of Book.author in a loop is the N+1 the action reports.
    names = [book.author.name for book in make_session().query(Book).all()]
    assert len(names) == 4
