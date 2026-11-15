import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import { nodeId, pytestArgs, reportOption, resolveReportPath, testAt } from "../../pytest";

const SOURCE = `import pytest


def helper():
    return 1


def test_plain(session):
    x = helper()

    assert x


class TestOrders:
    @pytest.mark.slow
    def test_method(self):
        def inner():
            return 2
        assert inner()

    async def test_async(self):
        pass


def outer():
    def test_hidden():
        pass
`.split("\n");

const line = (text: string) => SOURCE.findIndex((l) => l.includes(text));

test("testAt finds the test that encloses a line", () => {
  assert.deepEqual(testAt(SOURCE, line("def test_plain")), ["test_plain"]);
  assert.deepEqual(testAt(SOURCE, line("x = helper()")), ["test_plain"]);
  // a blank line inside the function
  assert.deepEqual(testAt(SOURCE, line("x = helper()") + 1), ["test_plain"]);
  assert.deepEqual(testAt(SOURCE, line("return 2")), ["TestOrders", "test_method"]);
  assert.deepEqual(testAt(SOURCE, line("assert inner()")), ["TestOrders", "test_method"]);
  assert.deepEqual(testAt(SOURCE, line("pass")), ["TestOrders", "test_async"]);
});

test("testAt finds no test outside test functions", () => {
  assert.equal(testAt(SOURCE, line("return 1")), undefined);
  assert.equal(testAt(SOURCE, line("import pytest")), undefined);
  assert.equal(testAt(SOURCE, line("class TestOrders")), undefined);
  // a test defined inside a function is not collected by pytest
  assert.equal(testAt(SOURCE, line("def test_hidden") + 1), undefined);
});

test("the node id is relative to pytest's directory", () => {
  const cwd = path.resolve("/work/backend");
  const file = path.join(cwd, "tests", "test_orders.py");
  assert.equal(nodeId(cwd, file, ["TestOrders", "test_method"]), "tests/test_orders.py::TestOrders::test_method");
});

test("the run writes the report the extension reads", () => {
  assert.equal(reportOption("/w/queryguard.json"), "--queryguard-json=/w/queryguard.json");
  assert.equal(reportOption("/w/out.SARIF"), "--queryguard-sarif=/w/out.SARIF");
  assert.deepEqual(pytestArgs("/w/q.json", ["-p", "no:randomly"], ["tests/t.py"]), [
    "-m",
    "pytest",
    "--queryguard-json=/w/q.json",
    "-p",
    "no:randomly",
    "tests/t.py",
  ]);
});

test("files of the report are looked up in each base in turn", () => {
  const a = path.resolve("/a");
  const b = path.resolve("/b");
  const existing = new Set([path.join(b, "app", "orders.py"), path.resolve("/abs/x.py")]);
  const exists = (p: string) => existing.has(p);
  assert.equal(resolveReportPath("app/orders.py", [a, b], exists), path.join(b, "app", "orders.py"));
  assert.equal(resolveReportPath("app/missing.py", [a, b], exists), undefined);
  assert.equal(resolveReportPath(path.resolve("/abs/x.py"), [a], exists), path.resolve("/abs/x.py"));
});
