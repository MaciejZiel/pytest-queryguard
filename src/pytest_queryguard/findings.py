from __future__ import annotations

from dataclasses import dataclass

# Finding kinds. They are part of the identity of a finding, so they never change.
KIND_RELATIONSHIP = "relationship-load"
KIND_REPEAT = "repeated-statement"
KIND_MISSING_INDEX = "missing-index"
KIND_COLUMN_LOAD = "column-load"
KIND_WRITE_LOOP = "write-loop"
KIND_UNUSED_EAGER_LOAD = "unused-eager-load"

SEVERITY_ERROR = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One problem a detector found, with its report text already rendered.

    ``kind`` and ``label`` say what was found: for ``relationship-load`` the
    relationship (``Order.customer``), for ``repeated-statement`` the
    normalized statement, for ``column-load`` the class and the deferred
    attributes (``Child.blob``) or its cause (``Parent (expired)``), for ``write-loop`` the normalized
    INSERT, UPDATE or DELETE, for ``unused-eager-load`` the relationship
    (``Order.customer``), for ``missing-index`` the table. ``file`` and
    ``function`` say where it first happened: ``file`` is relative to pytest's
    rootdir, with forward slashes, or as the interpreter reports it when the
    file is outside the rootdir; both are None when the call site is unknown.

    ``(kind, label, file, function)`` is the identity: it stays the same from
    run to run, whatever the line numbers and counts, and whatever directory
    the suite runs from. ``line`` and ``count`` are details of one run.
    """

    kind: str
    label: str
    file: str | None
    function: str | None
    message: str
    severity: str = SEVERITY_ERROR
    line: int | None = None
    count: int = 1

    @property
    def identity(self) -> tuple[str, str, str | None, str | None]:
        return (self.kind, self.label, self.file, self.function)
