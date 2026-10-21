# TW-658: Twistor Verification Engine (TDD evidence)

Prepared by Twistor Holdings LLC

- Ticket: TW-658, the verification engine build under TW-646 (Twistor Social), sequenced by spec Section 13 after the TW-649 benchmark harness (merged 42fdd95) and the TW-650 schema (merged 9707fc6).
- Legal basis: TW-654 decisions D1 to D3, approved by Justynn 2026-10-20. D1 adopts the BIPA-standard consent flow, D2 adopts the retention and destruction schedule, D3 keeps the production capture flag OFF until outside counsel's written review is filed to TW-654. This build is benchmark-facing only, and the flag discipline is enforced in code and proven by test.
- Sources of truth: spec TW-646 Section 5 (engine) and Section 6.7 (integrity lifecycle); the TW-648 OpenSpec package (tasks items 10 and 12 updated by this build); Angela's TW-654 package Section 1.4 item 1 (consent moments) and item 2 (retention); Rosa's TW-650 review (the consent-type trigger nit, closed here).
- Method: RED first, checkpoint commits kept unsquashed. RED at 38cd905 (five test files, collection errors because the engine did not exist). GREEN at 90e3aa7. One proof-driven fix at af9ff7c (PDF417 adapter, found by a live library probe, section 5).
- Branch: zeke/tw-658-verify-engine, worktree zeke-tw658-engine, off main at 9707fc6. Not merged; Rosa reviews first, per the standing gate.

## 1. What was built, per the brief

1. Document pipeline (brief item 1): stage 1 and 2 in `engine/crosschecks.py` and `engine/normalize.py`. OCR fields, MRZ (TD1, TD2, TD3 via the harness parser), and PDF417 AAMVA fields are normalized to canonical values and cross-checked field by field. MRZ check digits are verified by the harness code the benchmark scores with. Canonical source preference: MRZ for passports, barcode for United States licenses and state IDs, OCR as fallback. Disagreements across machine-readable sources land as named tamper signals. Date handling covers ISO, United States numeric, compact, MRZ six-digit (with the never-future rule for birth dates), and AAMVA spelled-month shapes. Document classes follow the v1 schema: government classes enabled; the school_id class shape exists end to end (consent class, document row, pipeline types) but its capture is not enabled in this phase, and the refusal is a tested behavior.
2. Face match, liveness, duplicate search (brief item 2): `engine/face.py` (cosine banding: match, review, no match; duplicate search over live templates), `engine/liveness.py` (MediaPipe landmark geometry: randomized active challenges from a four-move pool, eye-aspect and head-yaw scoring), passive MiniFASNet scoring through the harness stage. InsightFace is not imported anywhere; the embedding path is dlib via face_recognition, the same component the TW-649 FaceMatchStage scores with. Duplicate and stolen-photo search runs against stored templates at selfie time; a banned anchor anywhere in the match path refuses the attempt.
3. Decision discipline (brief item 3): on every outcome (pass, fail, review), staged images are wiped, the encrypted match payload is cleared, and both destructions are recorded as executed rows in the TW-650 `vfy_retention_actions` state machine with trigger purpose_satisfied. Only a pass persists a biometric template (AES-256-GCM ciphertext), an age attestation, and a verification state. Verdicts persist as session rows; extracted fields persist only as HMAC-SHA256 hashes in `vfy_documents`. Dormancy semantics from TW-650 are untouched.
4. Consent writes (brief item 4): `engine/consent.py` is the only consent writer: grant (versioned policy and retention schedule strings), require (the capture gate, evaluated before a session exists), withdraw (revoke, drop standing immediately, schedule template destruction through the retention queue with trigger verification_revocation). Rosa's TW-650 nit is closed in `supabase/migration_verify_v2.sql`: a deferred constraint trigger on `vfy_consents` revalidates the consent-type pairing when `consent_type` itself is updated, the side the v1 triggers do not watch. Behaviorally proven on live Postgres (section 4).
5. Benchmark integration (brief item 5): `engine/benchmarking.py` produces harness inputs with the engine's own components (pair scoring through the embed port and the engine cosine math; liveness presentations through the liveness port against the engine floor). `backend/app/verify/benchmark/gate.py` wires the gate run: the coverage set defaults from the registry entry; the consented-volunteer set takes its strata from a declared collection protocol record and refuses to run before the declaration exists; reports are stamped with the dataset id and the engine version. Collecting volunteers is not part of this build; only the wiring is. A synthetic end-to-end gate run (300 genuine and 5000 impostor pairs per group, two groups, deterministic embeddings) passes the gate in tests, labeled synthetic by construction.
6. Capture flag (brief item 6): `EngineConfig.production_capture_enabled` defaults to False and reads `VFY_PRODUCTION_CAPTURE_ENABLED` at deployment. `start_session` raises CaptureDisabled before anything else, including before consent and class checks; the API maps it to 403. Tests prove refusal at the pipeline level and at the HTTP level, and prove zero sessions, documents, biometrics, and staging refs exist after a refused attempt.

## 2. File manifest

Engine package, `backend/app/verify/engine/`:

| File | Lines | Job |
| --- | --- | --- |
| config.py | 90 | Thresholds as one frozen object; the D3 capture flag; version strings (engine, consent policy, retention schedule) |
| errors.py | 27 | Engine refusal types (capture disabled, class not enabled, consent required, session state, unknown session) |
| normalize.py | 150 | Field normalization, date parsing (five shapes), age bands and band graduation dates |
| hashing.py | 32 | HMAC-SHA256 field hashes and the domain-separated identity anchor hash |
| crosschecks.py | 169 | OCR line parsing, canonical field selection, MRZ and barcode agreement, tamper signals |
| face.py | 80 | Cosine similarity, banding, duplicate search over template records |
| liveness.py | 105 | Landmark geometry (eye aspect ratio, head yaw), randomized challenges, challenge evaluation |
| velocity.py | 60 | Attempt windows per document, device, network |
| verdict.py | 110 | The verdict combiner: fail conditions named, everything else review, pass confidence banding |
| crypto.py | 50 | AES-256-GCM encryptor for templates and match payloads, lazy import |
| staging.py | 104 | Image staging protocol; in-memory and AES-GCM encrypted-file implementations, 72-hour TTL constant |
| ports.py | 61 | The model boundary: seven port protocols and the PipelinePorts bundle |
| adapters.py | 142 | Real ports over the TW-649 harness stages (YuNet, MediaPipe, dlib, MiniFASNet with crop preprocessing; PDF417 on the current zxing-cpp API) |
| store.py | 461 | VerifyStore protocol, in-memory implementation, Supabase implementation over the vfy_ tables |
| consent.py | 106 | TW-654 D1 consent writes: grant, require, withdraw |
| pipeline.py | 594 | The session orchestrator, stages 1 to 5 with the decision discipline |
| benchmarking.py | 67 | Engine scorers feeding the TW-649 harness |
| __init__.py | 21 | Package documentation and exports |

Plus: `backend/app/verify/benchmark/gate.py` (99, gate-run wiring), `backend/app/verify/router.py` (223, the verdict-only API of spec Section 5.6, registered in `backend/app/main.py` under /api/v1), `supabase/migration_verify_v2.sql` (63, the consent-type update guard), and OpenSpec tasks updates (item 10 built, item 12 status note).

## 3. Tests

85 new tests, all passing, in five files:

| File | Tests | Coverage |
| --- | --- | --- |
| test_verify_engine_core.py | 39 | Config and flag parsing; normalization and date shapes; age bands and graduation dates; field and anchor hashing; cross-checks and agreement semantics; cosine banding and duplicate search; liveness geometry and challenges; velocity windows; the verdict combiner (including passive liveness and AI-face never failing alone); AES-GCM round trip and tamper refusal |
| test_verify_engine_pipeline.py | 23 | The full session flow with deterministic fake ports: pass (template, attestation, state, anchor persisted; images destroyed; payload cleared; retention rows executed), review bands, no-match fail, expired document fail, banned anchor fail with no template, stolen-photo duplicate review, consent required, school class refused, capture flag off with zero residue, session ownership, withdrawal (standing dropped, destruction scheduled), verdict shape, anchor lookup booleans, engine version pipeline config recorded |
| test_verify_engine_gate.py | 12 | Coverage defaulted from the registry; per-group insufficient-data honesty; volunteer strata declaration rules and the refuse-before-declaration gate; report stamping (dataset, engine version); synthetic end-to-end gate pass; engine pair scorer and liveness presenter shapes |
| test_verify_engine_migration.py | 6 | The v2 migration contract: deferred constraint trigger on vfy_consents watching UPDATE OF consent_type, the pairing rule in both directions, idempotency guard, no new tables, attribution header, manual DOWN section |
| test_verify_engine_router.py | 5 | HTTP surface mirroring the Social router's Identity JWT auth: 401 unauthenticated, 403 with the flag off, the verdict flow over HTTP, GET equality with the decision response, anchor lookup booleans |

Full suite on this branch: 977 passed, 21 failed, 12 collection errors. Clean main baseline, re-measured fresh this session before any edit: 892 passed, 21 failed, 12 errors. The failed and error node sets are byte-identical between baseline and branch (diff clean), so this build adds 85 passing tests and zero new failures. The pre-existing failures and the xmlsec collection errors are the known TW-471 baseline. Ruff check passes on all new files; ruff format applied. `backend/app/main.py` carries exactly the one pre-existing I001 import-order error it has at base (verified on the clean checkout), unchanged in count by the two added lines.

## 4. Live Postgres proof (throwaway cluster)

Migration v1 and v2 were executed on a scratch PostgreSQL 16.2 cluster (the TW-109 spike's pgserver build, copied to /tmp, run as user nobody, the same harness pattern TW-650 used). Disclosed shims, scratch copy only: a stub `idn_users` table (the one prerequisite the vfy migration names), and a no-op pgcrypto extension shim (the bundled build ships only plpgsql and vector; gen_random_uuid is server core since PG13, and Supabase ships real pgcrypto). Both migrations ran clean twice (idempotent). Five behavioral probes:

| Probe | Setup | Result |
| --- | --- | --- |
| A | biometric consent, passport document, biometric | Commits clean (v1 pairing holds) |
| B | Then UPDATE the consent's consent_type to school_id_biometric_capture | Commit FAILS: "consent type school_id_biometric_capture does not match document class passport" (the v2 guard; Rosa's nit, closed behaviorally) |
| C | Consent with no biometric or document; UPDATE consent_type | Commits clean (unpaired edits stay free) |
| D | school_id consent, school_id document, biometric | Commits clean |
| E | Then UPDATE that consent's consent_type to biometric_capture | Commit FAILS with the mirror-image message |

## 5. Real-stack probe (partial install, honest limits)

The sandbox /tmp is a 512 MB tmpfs, which filled during the first install attempt; the probe venv therefore lived on the home filesystem and was deleted after use. Installed cleanly: zxing-cpp and passporteye (plus numpy and Pillow). Through the real `Pdf417Adapter`, a generated AAMVA PDF417 barcode decoded to exactly the harness field shape (surname, given names, document number, birth and expiry dates). Stage availability on this partial stack, via the harness probe: mrz available, pdf417 available; ocr, face_detect, landmarks, face_match, liveness unavailable (the heavy stack is not installed here).

One live defect found and fixed in this build: the TW-649 harness stage calls `zxingcpp.read_barcodes(image_path)`, but zxing-cpp 3.x takes an image, not a path, and raises TypeError on a string. The harness tests stub the module, so the drift was invisible. The engine's Pdf417Adapter loads the image and calls the current API, reusing the harness AAMVA parser so field semantics stay single-sourced (commit af9ff7c). The harness stage itself still carries the stale call; flagged below for a TW-649 follow-up.

No accuracy numbers are claimed anywhere in this build. The gate criteria values in the harness (FMR 1e-4, FNMR 0.10, gap 0.05, 300 genuine, 5000 impostor floors) are unchanged; whether they stand as the launch gate is a later criteria decision for Rosa and Justynn, exactly as TW-649 left it.

## 6. What a benchmark machine needs (the real gate run)

Exact setup, in order:

1. A machine with a C++ toolchain and cmake (dlib compiles from source; this is the one hard requirement). Python 3.12.
2. `pip install -r backend/requirements-benchmark.txt` (numpy, paddleocr, paddlepaddle, passporteye, zxing-cpp, opencv-python-headless, mediapipe, face-recognition with dlib, onnxruntime). Expect the dlib build to take minutes.
3. Model files: the YuNet ONNX (`face_detection_yunet_2023mar.onnx`, OpenCV Zoo, MIT) and the MiniFASNet weights (minivision-ai Silent-Face-Anti-Spoofing, Apache-2.0). Point the environment at them: `VFY_YUNET_MODEL_PATH`, `VFY_MINIFASNET_MODEL_PATH`.
4. Datasets per the registry (`backend/app/verify/benchmark/registry.py`): FairFace (CC BY 4.0, scripted fetch) and LFW (research use) for the public sets; the consented-volunteer set only after the TW-654 collection protocol runs (a human step: consent collection, strata declaration via `declare_volunteer_strata`, then the run).
5. Smoke first: `run_gate` against `twistor-synth-smoke` with engine-scored synthetic pairs, then the real sets. One caution to verify in that first smoke: PaddleOCR 3.x return shapes differ from the 2.x nested-list shape the TW-649 OCR stage was written against; if the smoke shows it, the stage wrapper needs the same kind of adapter fix the PDF417 stage got here.
6. Environment for the engine service itself (not the gate): `VFY_TEMPLATE_KEY_B64` (32-byte AES key, base64), `VFY_HMAC_KEY`, `VFY_STAGING_DIR`, and `VFY_PRODUCTION_CAPTURE_ENABLED` left unset (off) until TW-654 D3 clears.

## 7. Design calls flagged for Rosa

1. Barcode agreement is computed only when a barcode actually decoded. An absent read cannot disagree; without this rule every passport (no PDF417 by design) would carry a permanent disagreement signal. Within a decoded barcode, the harness agreement semantics apply unchanged.
2. Velocity history persists as `velocity` rows in `vfy_signals` (detail carries the kind and key reference), because the v1 schema has no attempts table. Fits the signal audit model; a dedicated table is a schema change for a later ticket if volume argues for it.
3. The session's document class is working state (constructor and session view), not a `vfy_sessions` column; the durable class record is `vfy_documents.document_type`, written at the document stage. Consistent with the v1 schema as merged.
4. Liveness challenge state is process-local (issued at begin_liveness, evaluated at selfie). A restart mid-session loses the challenge set and the selfie stage issues a fresh set; no durable state is affected.
5. Consent withdrawal drops standing immediately (state revoked, tier 0) while template destruction executes through the retention action queue (trigger verification_revocation, due at once). The member is in the ghost town from the moment of withdrawal; the tombstoning is the retention job's executed step, never a hard delete by the engine.
6. The v2 guard constrains consent-type edits only where a biometric and document already pair the consent to a session. Editing an unpaired consent's type stays free (probe C). The alternative, forbidding consent_type updates outright, was rejected: a mistyped consent corrected before any capture is legitimate operations work.
7. Where a document extracts no name or number, `vfy_documents` still needs its NOT NULL hash columns, so the engine writes the HMAC of the empty string. The extraction failure itself is what the signals record; the hash column asserts nothing.
8. `Pdf417Adapter` bypasses the harness stage's decode call (section 5). The harness stage keeps its stale path call; a TW-649 follow-up should fix `stages.Pdf417Stage.decode_fields` to the image API so the benchmark and the engine share one decode path again.
9. The store protocol gained `get_age_attestation` so the verdict view's age band resolves identically on both store implementations.
10. The production pipeline factory (`router.get_pipeline`) reads its keys from environment variables named above. Deploy wiring (secrets provisioning, staging volume) belongs to the deploy lane (PR #9), not this build.

## 8. Known gaps, stated plainly

- No real accuracy measurement exists yet. The engine runs end to end against deterministic fake ports and against the real zxing-cpp and passporteye packages; the OCR, detection, embedding, and liveness models have not run in this sandbox (disk and toolchain limits, section 5). The launch gate run is a later step on a benchmark machine, per section 6.
- The human-review queue's reviewer tooling is the TW-650 service layer's surface; this build lands review verdicts in the session states that surface reads. No reviewer actions are built here.
- Door Pass photo consent (Angela's second consent moment) is a TW-653 surface; this build's consent module covers the Social T2 capture classes. The Door Pass photo flow reuses `vfy_consents` with its own text version when TW-653 builds.
- Synthetic gate numbers prove wiring only. They are labeled synthetic in the registry, in the report stamping, and in these tests, and are never presented as measured accuracy.
