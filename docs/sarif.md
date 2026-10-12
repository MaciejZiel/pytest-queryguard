# SARIF output

```console
pytest --queryguard-sarif=queryguard.sarif
```

writes the findings of the run as [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
the format GitHub code scanning reads. The existing `--queryguard-report`
flag is unrelated and unchanged. The path is relative to the directory pytest
was started from.

## What is in the file

- One rule per finding kind (`queryguard/relationship-load`,
  `queryguard/repeated-statement`, `queryguard/column-load`,
  `queryguard/write-loop`, `queryguard/missing-index`), each with a
  description and a fix hint. All five rules are always listed.
- One result per finding identity (kind, label, file, function), so the same
  finding from several tests is one result, with its counts added up and the
  earliest line.
- The location is the file relative to the rootdir (forward slashes) and the
  line of the first occurrence; the function is a logical location.
- `level` is `error` for a kind that fails tests and `warning` otherwise (see
  `queryguard_mode` and `queryguard_fail_on`).
- `partialFingerprints` are derived from the finding identity only, not from
  line numbers or counts. `primaryLocationLineHash`, the value GitHub
  de-duplicates alerts by, is set from it, so a finding keeps its alert when
  the code around it moves.
- Results are sorted and the output is stable, so two runs on the same code
  give the same bytes.

`primaryLocationLineHash` is normally a hash of the source line, which GitHub
computes itself when it is missing. pytest-queryguard sets it from the finding
identity instead, because a finding here is a pattern over many statements and
not one line, and a hash of the line would change with every edit near it. The
`:1` suffix is only the occurrence index that GitHub's format expects; it is
not a real count. This follows the format of the value, but whether GitHub code
scanning accepts and de-duplicates by an identity digest in that field has not
been verified. If it does not, the alert would be matched by location as for any
SARIF without the field.

## What is left out

- Findings ignored with `queryguard_ignore` or `allow_nplusone`.
- Findings accepted by the [baseline](baseline.md). They are left out instead
  of being marked with SARIF `suppressions`: GitHub's SARIF documentation does
  not say what it does with suppressed results, and leaving them out cannot
  produce an alert. A baselined finding that GitHub already knows then closes
  as fixed, and opens again if it leaves the baseline.
- Findings without a location inside the rootdir (an unknown call site, or a
  file outside the project). GitHub shows a result only when it has a
  location. The terminal summary says how many were left out.

The file is written at the end of every run, also when no finding was made,
so an upload step always has something to upload. With pytest-xdist, the
workers send their findings to the controller, which writes the file once.

An excerpt:

```json
{
  "ruleId": "queryguard/relationship-load",
  "ruleIndex": 0,
  "level": "error",
  "message": { "text": "Order.customer was lazy-loaded 3 times\nfirst at app/orders.py:8 in list_orders\n..." },
  "locations": [
    {
      "physicalLocation": {
        "artifactLocation": { "uri": "app/orders.py" },
        "region": { "startLine": 8 }
      },
      "logicalLocations": [{ "name": "list_orders", "kind": "function" }]
    }
  ],
  "partialFingerprints": {
    "primaryLocationLineHash": "6f1c...:1",
    "queryguardIdentity/v1": "6f1c..."
  }
}
```
