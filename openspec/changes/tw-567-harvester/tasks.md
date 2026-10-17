# TW-567 tasks

- [ ] 1. Scaffold `research/harvester/` package: `config.py` (sources.yaml
  loader + validation), `models.py` (Document/PropertyRecord dataclasses),
  `politeness.py` (RateLimiter, robots.txt check), `sources.yaml` (10
  enabled sources, verification status each)
- [ ] 2. `extract.py`: metadata normalization + heuristic material-property
  extraction (strength/modulus/impact/elongation/density/fiber fraction,
  unit normalization GPa->MPa, confidence labels)
- [ ] 3. `store.py`: SQLite schema (sources, harvest_runs, documents,
  property_records), dedupe on external_id/DOI, run logging
- [ ] 4. Adapters: `base.py` interface; `crossref.py`, `openaire.py`,
  `doaj.py`, `arxiv.py`, `semanticscholar.py` (enrichment), `openalex.py`
  (graceful 429 degrade), `oai_pmh.py` (resumption-token paging)
- [ ] 5. `cli.py`: `init-db`, `run --query --sources --db --max-records
  --fetch-pdfs`, per-item fault isolation (one bad record never kills
  a run), JSON run summary
- [ ] 6. Tests (TDD): politeness, extraction, store, one API adapter
  fixture test, OAI-PMH fixture test; fixtures under `tests/fixtures/`
- [ ] 7. Seed run against enabled sources; corpus DB at
  `~/workspace/twistor-lab/data-layer/corpus/research_corpus.db`
  (outside the repo); record counts in ticket comment
- [ ] 8. Evidence report `docs/evidence/TW-567-harvester.tdd.md`;
  `ruff` clean; dispatch Rosa review on the diff
