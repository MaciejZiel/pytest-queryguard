from app.orders import indexed_orders_of


def test_orders_of(session):
    assert len(indexed_orders_of(session, 1)) == 2
