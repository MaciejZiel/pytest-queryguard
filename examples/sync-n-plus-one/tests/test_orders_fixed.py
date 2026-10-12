import pytest
from app.customers_fixed import fetch_customers, rename_customers
from app.orders_fixed import list_orders


def test_list_orders(session):
    assert len(list_orders(session)) == 50


def test_fetch_customers(session):
    assert len(fetch_customers(session, list(range(1, 51)))) == 50


@pytest.mark.max_queries(2)
def test_rename_customers(session):
    rename_customers(session, {1: "Ada", 2: "Grace"})
