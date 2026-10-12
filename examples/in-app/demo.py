"""Send a few requests to the example apps and print what queryguard reports.

Run from this directory: python demo.py
"""

import logging
import sys

from fastapi.testclient import TestClient

logging.basicConfig(stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")


def fastapi_demo() -> None:
    from app.main import app

    with TestClient(app) as client:
        client.get("/orders")
        client.get("/orders-fixed")
        client.get("/orders/3")
        data = client.get("/_queryguard/requests.json").json()
    print("--- the panel's requests.json, newest first ---")
    for request in data["requests"]:
        print(
            request["name"],
            request["status"],
            f"{request['statement_count']} statements",
            f"budget {request['budget']}",
            [f["label"] for f in request["findings"]],
        )


def flask_demo() -> None:
    from app.flask_app import app

    print("--- Flask ---")
    app.test_client().get("/orders").close()


if __name__ == "__main__":
    fastapi_demo()
    flask_demo()
