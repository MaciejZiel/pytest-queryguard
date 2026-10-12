"""The accuracy corpus: realistic ORM code with a known verdict each.

``scenarios/`` holds one small module per scenario; its docstring says whether
it is a BUG (a detector must flag it, with the stated finding kinds) or CLEAN
(nothing may be flagged) and why. ``runner.py`` runs them through the plugin,
``test_corpus.py`` turns the expectations into ordinary tests, and
``scripts/accuracy_report.py`` computes precision and recall from the same run.
"""
