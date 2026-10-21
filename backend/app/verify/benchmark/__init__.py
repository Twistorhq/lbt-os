"""Verification engine benchmark harness (TW-649).

Metric definitions for the TW-646 Section 5.3 launch gate: end-to-end
verify accuracy and false-match / false-non-match rates, broken out by
demographic group, computed from scored attempts produced by the real
FOSS stack on registered datasets (see registry.py and README.md).
Pure stdlib: the metric core never depends on the heavy model libraries.
"""
