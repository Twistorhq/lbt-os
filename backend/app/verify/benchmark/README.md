# Verification Engine Benchmark Harness (TW-649)

Prepared by Twistor Holdings LLC

The launch gate for Twistor Social's T2 verification tier is a
benchmark we run ourselves, on our own FOSS stack, before the tier
opens (TW-646 Section 5.3). This harness is that benchmark: scored
attempts in, a launch-gate verdict out, with false-match and
false-non-match rates broken out by demographic group. If a group
sees materially worse performance, the tier does not launch until the
gap is addressed. Measured, not assumed.

## Layout

| Module | Job |
|---|---|
| `metrics.py` | The gate math: FMR/FNMR, per-group rates, demographic gap, APCER/BPCER (ISO 30107-3), gate evaluation. Pure stdlib. |
| `documents.py` | MRZ check digits (ICAO 9303), OCR field accuracy, front-to-barcode agreement. Pure stdlib. |
| `registry.py` | Dataset registry: every dataset's license and consent basis, recorded before use. |
| `stages.py` | Lazy adapters for the FOSS pipeline stages (below). Heavy libraries are optional at import time and reported honestly when absent. |
| `runner.py` | End-to-end: attempts to a JSON-serializable gate report; deterministic synthetic attempts for smoke runs. |

## The gate (TW-646 Section 5.3, as code)

`GateCriteria` defaults carry the spec's planning values:

- Overall and per-group FMR ceiling: **1 in 10,000** (1e-4).
- Per-group FNMR ceiling: 10% (planning value).
- Demographic FNMR gap ceiling: 5 points between best and worst
  measured group (planning value).
- Minimum samples per group: 300 genuine / 5,000 impostor attempts
  (planning values). Below the floor the verdict is
  **INSUFFICIENT_DATA**, never a silent pass.
- Expected-group coverage: gate-bearing datasets declare the
  demographic groups a run must cover (registry.py
  `expected_groups`). A run missing an expected group reports
  **INSUFFICIENT_DATA** for that group by name; the gate never
  passes on just the groups that happened to show up.
- A measured failure outranks insufficient data: a breach we can see
  is a breach.

Final numbers are set with the measured report in front of Rosa and
Justynn before T2 opens. There is no vendor fallback (decided
2026-10-20, TW-646 Section 12 Q3): if the gate fails, T2 does not
open until the FOSS engine passes on its own. No paid check, no
fallback path, no bridge, automatic or otherwise.

One constraint on those final numbers, recorded now so the
criteria-setting session does not discover it mid-meeting: the FMR
ceiling and the impostor floor interact. At the planning floor of
5,000 impostor attempts per group, a single false match yields FMR
2e-4, already above the 1e-4 ceiling, so at floor sample sizes the
ceiling effectively demands zero false matches. And 5,000 attempts
cannot statistically resolve a 1e-4 rate: zero false matches in
5,000 trials bounds the true FMR only to roughly 6e-4 at 95 percent
confidence. The session has to choose: impostor floors on the order
of 30,000 or more per group, or a ceiling that floor-size samples
can actually resolve.

## Run it

Tests (pure stdlib, no model stack needed):

    cd backend
    python -m pytest tests/test_verify_benchmark.py tests/test_verify_benchmark_stages.py

Smoke run (synthetic attempts, smoke scale, NOT gate evidence):

    python -m app.verify.benchmark.runner --seed 7 --out smoke-report.json

Stage availability on this machine:

    python -c "from app.verify.benchmark.stages import probe_stages; [print(s) for s in probe_stages()]"

Real dataset runs need the model stack:

    pip install -r requirements-benchmark.txt

## Datasets (registry summary; registry.py is authoritative)

| Id | Status | Basis |
|---|---|---|
| `twistor-synth-smoke` | available | In-house synthetic scores; no real people; smoke scale only. |
| `twistor-consented-volunteers` | planned | Written BIPA-standard consent (TW-654 package); self-described group labels. The set the real gate runs on. |
| `lfw` | planned | Public research release; sanity reference only; no group labels used. |
| `fairface` | planned | CC-licensed source images; candidate for the per-group breakout. |

Rules: synthetic and properly consented / public test data only. No
scraped faces, no production data, nothing a member sent us. Group
labels come from the dataset; the harness never invents them.

## Stages and licenses

PaddleOCR (Apache-2.0) for OCR; passporteye (MIT) plus ICAO 9303
check digits for MRZ; zxing-cpp (Apache-2.0) for PDF417; OpenCV YuNet
(MIT) for face detection; MediaPipe Face Mesh (Apache-2.0) for
landmarks; dlib face recognition via face_recognition (Boost /
freely usable model) for the 1:1 match, with distances normalized to
[0, 1] similarities; MiniFASNet via ONNX Runtime (Apache-2.0 / MIT)
for the passive liveness signal, which is one signal and never a
gate by itself.

**InsightFace is excluded**: its pretrained model packs are licensed
for non-commercial research only. Unclear or non-commercial weights
do not enter this pipeline, even as a benchmark reference.

## Smoke report

`examples/smoke-report.json` is a committed example of the report
shape, produced from 240 synthetic attempts (seed 7): overall FMR
0.0083, FNMR 0.0, verdict **FAIL** with INSUFFICIENT_DATA also
flagged. Both halves of that verdict are the harness working as
designed: the sample floors are nowhere near met (120 attempts per
class against floors of 300 genuine / 5,000 impostor per group), and
one synthetic impostor in one group still crossed the threshold,
which is a measured FMR-ceiling breach, and a measured failure
outranks insufficient data (Section: the gate, above). Smoke numbers
are never gate evidence.
