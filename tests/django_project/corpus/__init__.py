"""The Django accuracy corpus: labelled BUG and CLEAN scenarios, as tests/corpus.

Each scenario module in ``scenarios`` defines ``VERDICT``, ``KINDS``, an
optional ``KNOWN`` ({configuration: reason} where the plugin is known to
disagree), an optional ``setup()`` (not measured, after the seed rows of
``library.seed``) and ``run()``, the code under test.
"""
