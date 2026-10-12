from app.orders import orders_of


def test_orders_of(session):
    assert len(orders_of(session, 1)) == 2
