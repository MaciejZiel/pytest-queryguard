import os

from flask import Flask, request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, Customer, Order
from pytest_queryguard import app as queryguard

engine = create_engine(
    "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
)
Base.metadata.create_all(engine)
with Session(engine) as session:
    session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 6))
    session.add_all(Order(id=i, customer_id=i) for i in range(1, 6))
    session.commit()

app = Flask(__name__)

# Development only: never in production.
if os.environ.get("APP_ENV") == "development":
    queryguard.watch(engine)
    app.wsgi_app = queryguard.QueryGuardWSGI(
        app.wsgi_app, budgets={"GET /orders/<int:order_id>": 1}
    )

    @app.before_request
    def name_the_route() -> None:
        # WSGI has no route templates; tell queryguard the Flask rule.
        if request.url_rule is not None:
            queryguard.set_route(request.url_rule.rule)


@app.get("/orders")
def list_orders() -> list[str]:
    with Session(engine) as session:
        names = []
        for order in session.scalars(select(Order)):
            names.append(order.customer.name)
        return names
