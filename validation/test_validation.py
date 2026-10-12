# Comment line that shifts every line number by one.
from sqlalchemy import select

from models import Book, Order, make_session


def test_author_names_n_plus_one():
    names = [book.author.name for book in make_session().query(Book).all()]
    assert len(names) == 4


def test_orders_filter_without_index():
    session = make_session()
    session.add_all(Order(id=i, customer_id=i % 3, total=i) for i in range(1, 7))
    session.commit()
    rows = session.scalars(select(Order).where(Order.customer_id == 1)).all()
    assert len(rows) == 2
