# Prepared by Twistor Holdings LLC
"""TW-838 quantum lane: cross-vendor QPU calibration telemetry time series.

Shape 1 of the TW-838 lane brief (score 82/100, BUILD THE MOAT): vendors
publish the current calibration snapshot and overwrite it; this lane keeps
the history. IBM fleet first (this package), Braket behind an adapter
interface (gated on the AWS account Justynn creates).

Legal basis (Angela's terms read, 2026-10-30, TW-838): DERIVED-ONLY product.
Production collection runs under an IBM Cloud Pay-As-You-Go account (CSA
basis). The free Open Plan (EUA 2(d)) is licensed for research, education,
testing and evaluation only, so the poller caps Open-Plan runs at genuine
code-testing scale. Raw calibration values stay in bronze, internal only,
at every price point; only genuinely computed products (device-day
aggregates, drift features, indexes) are served.

PII: none exists in any pipe in this lane (machine telemetry and published
device specifications only). The medallion still applies for lineage.
"""

LANE = "quantum"
VENDOR_IBM = "ibm"
