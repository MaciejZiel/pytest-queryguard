from library import author_names

from models import make_session


def test_author_names_n_plus_one():
    assert len(author_names(make_session())) == 4
