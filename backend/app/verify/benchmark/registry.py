"""Dataset registry for the verification benchmark (TW-649).

Every dataset the harness may touch is registered here BEFORE use,
with its license and its consent basis written down. The registry is
an audit artifact: if an entry cannot state both plainly, the dataset
does not run. Rules, per the TW-649 brief and TW-646 Section 5.3:

- Synthetic and properly consented / public test data ONLY.
- No scraped faces. No production data. Nothing a member ever sent us.
- A dataset's demographic group labels come from the dataset itself;
  the harness reports groups, it never invents or infers them.
- Gate-bearing entries declare `expected_groups`: the demographic
  groups a run of that dataset is expected to cover. A run missing
  an expected group reports INSUFFICIENT_DATA for it (see
  metrics.evaluate_gate); an absent group never reads as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    modality: str  # what a run on this dataset measures
    license: str
    consent_basis: str
    status: str  # "available" | "registered" | "planned"
    notes: str = ""
    # Demographic groups a run of this dataset must cover; the gate
    # reports each absent one as INSUFFICIENT_DATA. Empty for entries
    # that cannot feed the per-group gate.
    expected_groups: tuple[str, ...] = ()


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="twistor-synth-smoke",
        title="Twistor synthetic smoke attempts",
        modality="face-match scores (synthetic distributions, no images)",
        license="Generated in-house by runner.synthetic_attempts; Twistor-owned.",
        consent_basis=(
            "No real people. Scores are drawn from seeded synthetic "
            "distributions; there is no face, no image, and no subject "
            "to consent."
        ),
        status="available",
        notes=(
            "Smoke scale only: proves the harness math end to end. "
            "Numbers from this dataset are never gate evidence."
        ),
    ),
    Dataset(
        id="twistor-consented-volunteers",
        title="Twistor consented volunteer evaluation set",
        modality="ID-photo-to-selfie face match, document OCR, liveness",
        license=(
            "Twistor-owned; collected under written agreement for "
            "benchmark evaluation only."
        ),
        consent_basis=(
            "Explicit written informed consent from every volunteer, "
            "collected to the BIPA-standard flow drafted in the TW-654 "
            "legal gates package: stated purpose (launch-gate "
            "evaluation), retention limit, destruction schedule, "
            "revocable. Demographic group labels are self-described "
            "by the volunteer."
        ),
        status="planned",
        notes=(
            "The set the real launch gate runs on (TW-646 Section 5.3). "
            "BLOCKER: collection cannot start until Angela's consent "
            "package is approved and Justynn signs off (TW-654 D1-D3). "
            "expected_groups is recorded here from the collection "
            "protocol's coverage strata before the first gate run "
            "(labels are self-described, so the set cannot be fixed "
            "until the protocol exists); a gate run passes that set "
            "to the gate, and any expected group absent from the run "
            "is INSUFFICIENT_DATA, never silent."
        ),
    ),
    Dataset(
        id="lfw",
        title="Labeled Faces in the Wild (LFW)",
        modality="face-match pairs (standard 6,000-pair protocol)",
        license=(
            "University of Massachusetts Amherst; distributed for "
            "research evaluation. No commercial redistribution."
        ),
        consent_basis=(
            "Public-figure news photographs collected in 2007; subjects "
            "did not explicitly consent to dataset inclusion. Historical "
            "research-use release only: offline evaluation, never "
            "training, never production."
        ),
        status="planned",
        notes=(
            "Saturated celebrity benchmark (TW-646 Section 5.3): useful "
            "as a pipeline sanity reference, NOT as evidence of "
            "ID-photo-to-selfie performance. No demographic labels are "
            "used from LFW; it cannot feed the per-group gate."
        ),
    ),
    Dataset(
        id="fairface",
        title="FairFace",
        modality="face images with dataset-provided demographic labels",
        license=(
            "KAIST AI; images sourced from Flickr under Creative "
            "Commons licenses; dataset released for research use."
        ),
        consent_basis=(
            "Photographs published by their creators under Creative "
            "Commons licenses permitting reuse; dataset assembled for "
            "bias auditing of face models. Offline evaluation only."
        ),
        status="planned",
        notes=(
            "Candidate for the per-group FNMR/FMR breakout before the "
            "volunteer set exists. Web-photo domain, not ID photos: "
            "results bound expectations, they do not clear the gate. "
            "expected_groups declares the race labels FairFace itself "
            "publishes; a breakout run missing any of them reports "
            "INSUFFICIENT_DATA for that group."
        ),
        expected_groups=(
            "White",
            "Black",
            "Indian",
            "East Asian",
            "Southeast Asian",
            "Middle Eastern",
            "Latino_Hispanic",
        ),
    ),
)


def get(dataset_id: str) -> Dataset:
    for entry in DATASETS:
        if entry.id == dataset_id:
            return entry
    raise KeyError(f"dataset not registered: {dataset_id!r}")


def by_status(status: str) -> tuple[Dataset, ...]:
    return tuple(d for d in DATASETS if d.status == status)
