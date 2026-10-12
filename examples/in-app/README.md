# In-application mode

A FastAPI app (`app/main.py`, async SQLAlchemy) and a Flask app
(`app/flask_app.py`, sync SQLAlchemy) with the in-application mode switched
on: `watch(engine)` and the ASGI or WSGI middleware, a query budget on one
route and, for FastAPI, the development panel. Development only: the apps
switch it on only while `APP_ENV` is `development` (the default here).

```console
$ pip install ../.. fastapi flask aiosqlite httpx
$ python demo.py
```

`demo.py` sends requests with the frameworks' test clients and prints what
queryguard logs, then the panel's JSON. The N+1 endpoint:

```text
WARNING pytest_queryguard.app: queryguard: GET /orders: 6 statements

queryguard: N+1 detected

  Order.customer was lazy-loaded 5 times
  first at app/main.py:48 in list_orders
```

The route over its budget of one statement:

```text
WARNING pytest_queryguard.app: queryguard: GET /orders/3 (route /orders/{order_id}): 2 statements

queryguard: expected at most 1 queries, got 2
```

The panel's JSON, newest first:

```text
GET /orders/3 200 2 statements budget 1 []
GET /orders-fixed 200 2 statements budget None []
GET /orders 200 6 statements budget None ['Order.customer']
```

The Flask view with the same loop:

```text
  Order.customer was lazy-loaded 5 times
  first at app/flask_app.py:39 in list_orders
```

To see the panel in a browser, run the FastAPI app with an ASGI server
(`uvicorn app.main:app`) and open http://127.0.0.1:8000/_queryguard/ after a
few requests. See
[docs/in-app.md](https://maciejziel.github.io/pytest-queryguard/in-app.html).
