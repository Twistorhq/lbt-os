# Prepared by Twistor Holdings LLC
"""TW-612 real-data ingestors: shared framework.

Medallion discipline for real data:
  bronze  - raw files, content-hashed (sha256), immutable, never mutated.
            Every artifact carries a license_tag + license_verdict.
  silver  - harmonized records with lineage to bronze hashes. Method codes
            preserved, never converted silently.
  gold    - ONLY records whose license_verdict is GREEN (redistributable).
            NC / restricted / unknown licenses are quarantined out of gold
            by construction: promote_to_gold() refuses anything else.

PII doctrine (TW-552): source contact info is hashed at ingestion, never raw.
"""

from __future__ import annotations

LANE_SUBSEASONAL = "subseasonal"
LANE_CROP_CLIMATE = "crop_climate"
LANE_CARBON = "carbon"

WAVE1_LANES = (LANE_SUBSEASONAL, LANE_CROP_CLIMATE, LANE_CARBON)
