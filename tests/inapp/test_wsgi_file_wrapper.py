"""The server must receive its own file-wrapper object, not a response proxy."""

from __future__ import annotations

import io
import os
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from wsgiref.handlers import SimpleHandler
from wsgiref.util import FileWrapper

import pytest
from flask import Flask, send_file
from sqlalchemy import text
from sqlalchemy.engine import Engine
from werkzeug.test import EnvironBuilder

from pytest_queryguard import QueryGuardWarning
from pytest_queryguard import app as queryguard
from pytest_queryguard.app import _core


class SlottedWrapper:
    """A native wrapper that cannot have close() replaced on the instance."""

    __slots__ = ("block_size", "filelike")

    def __init__(self, filelike: Any, block_size: int = 8192) -> None:
        self.filelike = filelike
        self.block_size = block_size

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        data = self.filelike.read(self.block_size)
        if not data:
            raise StopIteration
        return data

    def close(self) -> None:
        close = getattr(self.filelike, "close", None)
        if close is not None:
            close()


def _execute(engine: Engine, value: int) -> None:
    with engine.connect() as connection:
        connection.execute(text(f"SELECT {value}")).scalar_one()


def _environ(wrapper: Any = FileWrapper) -> dict[str, Any]:
    return {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/download",
        "wsgi.file_wrapper": wrapper,
    }


@pytest.mark.parametrize("wrapper", [FileWrapper, SlottedWrapper])
@pytest.mark.parametrize("consume", ["all", "part", "none"])
def test_native_identity_and_file_queries_survive_until_server_close(
    watched: Engine, wrapper: Any, consume: str
) -> None:
    closed: list[bool] = []
    made: list[Any] = []

    class Download(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            data = super().read(size)
            if data:
                _execute(watched, 102)
            return data

        def close(self) -> None:
            closed.append(True)
            _execute(watched, 103)
            super().close()

    download = Download(b"abcdef")

    def bare(environ: dict[str, Any], start: Any) -> Any:
        _execute(watched, 101)
        start("200 OK", [])
        result = environ["wsgi.file_wrapper"](download, 3)
        made.append(result)
        return result

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    environ = _environ(wrapper)
    with queryguard.track("outer", report=[]) as outer:
        body = app(environ, lambda *a: None)
        assert body is made[0]
        assert isinstance(body, wrapper)
        assert environ["wsgi.file_wrapper"] is wrapper
        assert app.reports == ()
        if consume == "all":
            assert list(body) == [b"abc", b"def"]
        elif consume == "part":
            assert next(iter(body)) == b"abc"
        assert app.reports == ()  # the native server still owes close()
        _execute(watched, 104)
        body.close()
        body.close()
        _execute(watched, 105)
    assert closed == [True]
    assert download.closed
    assert len(app.reports) == 1
    expected = (
        ["SELECT 101"]
        + ["SELECT 102"] * {"all": 2, "part": 1, "none": 0}[consume]
        + ["SELECT 103"]
    )
    assert [q.sql for q in app.reports[0].statements] == expected
    assert [q.sql for q in outer.statements] == ["SELECT 104", "SELECT 105"]


def test_flask_send_file_returns_the_native_wrapper(
    tmp_path: Path, watched: Engine
) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(b"a file response")
    flask_app = Flask(__name__)

    @flask_app.get("/download")
    def download() -> Any:
        _execute(watched, 201)
        return send_file(path)

    app = queryguard.QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    environ = EnvironBuilder(path="/download").get_environ()
    environ["wsgi.file_wrapper"] = FileWrapper
    body = app(environ, lambda *a: None)
    assert isinstance(body, FileWrapper)
    assert b"".join(body) == b"a file response"
    body.close()
    assert app.reports[0].statement_count == 1
    assert app.reports[0].status == 200


def test_native_descriptor_transfer_keeps_position_and_length(
    tmp_path: Path, watched: Engine
) -> None:
    path = tmp_path / "native.bin"
    path.write_bytes(b"0123456789")
    raw = path.open("rb", buffering=0)
    raw.seek(2)
    closed: list[bool] = []

    class Download:
        def read(self, size: int = -1) -> bytes:
            raise AssertionError("the native path must bypass Python read()")

        def fileno(self) -> int:
            return raw.fileno()

        def tell(self) -> int:
            return raw.tell()

        def close(self) -> None:
            closed.append(True)
            _execute(watched, 301)
            raw.close()

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start("200 OK", [("Content-Length", "3")])
        return environ["wsgi.file_wrapper"](Download())

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    try:
        body = app(_environ(), lambda *a: None)
        assert isinstance(body, FileWrapper)
        assert body.filelike.tell() == 2
        assert os.read(body.filelike.fileno(), 3) == b"234"
        body.close()
        assert closed == [True]
        assert [q.sql for q in app.reports[0].statements] == ["SELECT 301"]
    finally:
        raw.close()


@pytest.mark.parametrize("failure", ["read", "close", None])
def test_native_file_errors_and_warn_as_error_preserve_the_application_error(
    watched: Engine, failure: str | None
) -> None:
    class Download(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            _execute(watched, 401)
            if failure == "read":
                raise RuntimeError("download read failed")
            return super().read(size)

        def close(self) -> None:
            _execute(watched, 402)
            super().close()
            if failure == "close":
                raise RuntimeError("download close failed")

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start("200 OK", [])
        return environ["wsgi.file_wrapper"](Download(b"ok"))

    app = queryguard.QueryGuardWSGI(bare, panel=True, default_budget=0, report="warn")
    with warnings.catch_warnings():
        warnings.simplefilter("error", QueryGuardWarning)
        with queryguard.track("outer", report=[]) as outer:
            body = app(_environ(), lambda *a: None)
            assert isinstance(body, FileWrapper)
            if failure == "read":
                with pytest.raises(RuntimeError, match="download read failed"):
                    list(body)
                body.close()  # a budget warning must not hide the read error
            elif failure:
                assert b"".join(body) == b"ok"
                with pytest.raises(RuntimeError):
                    body.close()
            else:
                assert b"".join(body) == b"ok"
                body.close()  # the budget error is logged, not raised
            body.close()
            _execute(watched, 403)
    assert len(app.reports) == 1
    assert app.reports[0].over_budget
    assert [q.sql for q in outer.statements] == ["SELECT 403"]


def test_an_unused_file_wrapper_does_not_finish_a_different_response(
    watched: Engine,
) -> None:
    class Download(io.BytesIO):
        def close(self) -> None:
            _execute(watched, 501)
            super().close()

    def bare(environ: dict[str, Any], start: Any) -> Iterator[bytes]:
        unused = environ["wsgi.file_wrapper"](Download(b"unused"))

        def body() -> Iterator[bytes]:
            unused.close()
            _execute(watched, 502)
            yield b"different response"

        start("200 OK", [])
        return body()

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    response = app(_environ(), lambda *a: None)
    assert b"".join(response) == b"different response"
    response.close()  # type: ignore[attr-defined]
    assert [q.sql for q in app.reports[0].statements] == ["SELECT 501", "SELECT 502"]


def test_read_only_file_still_finishes_a_report() -> None:
    class ReadOnly:
        def read(self, size: int = -1) -> bytes:
            return b""

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start("200 OK", [])
        return environ["wsgi.file_wrapper"](ReadOnly())

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    body = app(_environ(), lambda *a: None)
    assert isinstance(body, FileWrapper)
    assert list(body) == []
    body.close()
    assert len(app.reports) == 1


def test_fail_open_leaves_the_server_factory_and_response_untouched(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(_core, "_error", RuntimeError("disabled"))
    made: list[Any] = []

    def bare(environ: dict[str, Any], start: Any) -> Any:
        assert environ["wsgi.file_wrapper"] is FileWrapper
        start("200 OK", [])
        result = FileWrapper(io.BytesIO(b"ok"))
        made.append(result)
        return result

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    body = app(_environ(), lambda *a: None)
    assert body is made[0]
    assert b"".join(body) == b"ok"
    body.close()
    assert app.reports == ()


def test_reference_server_takes_its_native_file_path(
    tmp_path: Path, watched: Engine
) -> None:
    path = tmp_path / "sendfile.bin"
    path.write_bytes(b"0123456789")
    raw = path.open("rb", buffering=0)
    raw.seek(2)
    native_transfers: list[Any] = []

    class File:
        def read(self, size: int = -1) -> bytes:
            raise AssertionError("the reference server should use its native path")

        def fileno(self) -> int:
            return raw.fileno()

        def close(self) -> None:
            _execute(watched, 601)
            raw.close()

    class NativeHandler(SimpleHandler):
        wsgi_file_wrapper = SlottedWrapper

        def sendfile(self) -> bool:
            assert isinstance(self.result, SlottedWrapper)
            native_transfers.append(self.result)
            self.write(os.read(self.result.filelike.fileno(), 3))
            return True

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start(
            "200 OK",
            [("Content-Type", "application/octet-stream"), ("Content-Length", "3")],
        )
        return environ["wsgi.file_wrapper"](File())

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    output, errors = io.BytesIO(), io.StringIO()
    environ = {
        "REQUEST_METHOD": "GET",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "PATH_INFO": "/download",
    }
    handler = NativeHandler(io.BytesIO(), output, errors, environ)
    try:
        handler.run(app)
        assert errors.getvalue() == ""
        assert len(native_transfers) == 1
        headers, body = output.getvalue().split(b"\r\n\r\n", 1)
        assert b"200 OK" in headers and b"Content-Length: 3" in headers
        assert body == b"234"
        assert raw.closed
        assert [q.sql for q in app.reports[0].statements] == ["SELECT 601"]
    finally:
        raw.close()


def test_interleaved_downloads_keep_separate_request_contexts(watched: Engine) -> None:
    class File(io.BytesIO):
        def __init__(self, uid: int) -> None:
            super().__init__(b"ok")
            self.uid = uid

        def read(self, size: int = -1) -> bytes:
            _execute(watched, self.uid)
            return super().read(size)

        def close(self) -> None:
            _execute(watched, self.uid + 10)
            super().close()

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start("200 OK", [])
        return environ["wsgi.file_wrapper"](File(environ["uid"]))

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    with queryguard.track("outer", report=[]) as outer:
        first = app({**_environ(), "uid": 701, "PATH_INFO": "/first"}, lambda *a: None)
        second = app(
            {**_environ(), "uid": 702, "PATH_INFO": "/second"}, lambda *a: None
        )
        assert isinstance(first, FileWrapper) and isinstance(second, FileWrapper)
        assert next(first) == next(second) == b"ok"
        second.close()
        first.close()
        _execute(watched, 799)
    assert [(r.path, [q.sql for q in r.statements]) for r in app.reports] == [
        ("/second", ["SELECT 702", "SELECT 712"]),
        ("/first", ["SELECT 701", "SELECT 711"]),
    ]
    assert [q.sql for q in outer.statements] == ["SELECT 799"]


def test_factory_failure_finishes_the_request_and_restores_its_context(
    watched: Engine,
) -> None:
    def broken_factory(file: Any) -> Any:
        _execute(watched, 801)
        raise RuntimeError("native constructor failed")

    def bare(environ: dict[str, Any], start: Any) -> Any:
        return environ["wsgi.file_wrapper"](io.BytesIO())

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    with queryguard.track("outer", report=[]) as outer:
        with pytest.raises(RuntimeError, match="native constructor failed"):
            app(_environ(broken_factory), lambda *a: None)
        _execute(watched, 802)
    assert app.reports[0].status == 500
    assert [q.sql for q in app.reports[0].statements] == ["SELECT 801"]
    assert [q.sql for q in outer.statements] == ["SELECT 802"]


def test_native_transfer_error_is_not_replaced_by_a_budget_warning(
    watched: Engine,
) -> None:
    class Download(io.BytesIO):
        def close(self) -> None:
            _execute(watched, 901)
            super().close()

    def bare(environ: dict[str, Any], start: Any) -> Any:
        start("200 OK", [])
        return environ["wsgi.file_wrapper"](Download(b"ok"))

    app = queryguard.QueryGuardWSGI(bare, panel=True, default_budget=0, report="warn")
    with warnings.catch_warnings():
        warnings.simplefilter("error", QueryGuardWarning)
        body = app(_environ(), lambda *a: None)
        assert isinstance(body, FileWrapper)
        with pytest.raises(ConnectionResetError, match="client disconnected"):
            try:
                # A native sendfile() error happens outside Python file methods.
                raise ConnectionResetError("client disconnected")
            finally:
                body.close()
        body.close()
    assert len(app.reports) == 1
    assert app.reports[0].statement_count == 1


def test_outer_middleware_sees_what_the_application_writes_into_environ(
    watched: Engine,
) -> None:
    def bare(environ: dict[str, Any], start: Any) -> Any:
        _execute(watched, 601)
        environ["REMOTE_USER"] = "alice"
        start("200 OK", [])
        return environ["wsgi.file_wrapper"](io.BytesIO(b"ok"))

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    environ = _environ()
    body = app(environ, lambda *a: None)
    assert environ["REMOTE_USER"] == "alice"
    assert environ["wsgi.file_wrapper"] is FileWrapper
    assert isinstance(body, FileWrapper)
    assert b"".join(body) == b"ok"
    body.close()
    assert [q.sql for q in app.reports[0].statements] == ["SELECT 601"]
