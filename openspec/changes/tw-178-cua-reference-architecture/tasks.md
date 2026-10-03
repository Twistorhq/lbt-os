# TW-178 tasks

**Prepared by Twistor Holdings LLC**

- [ ] 1. Verify licenses live: cua root LICENSE.md (MIT), cua-som PyPI
      license (AGPL-3.0-or-later) — record in proposal. DONE 2026-09-23.
- [ ] 2. Study cua `BaseComputerInterface` method surface (verified live
      2026-09-23). DONE.
- [x] 3. RED: write `backend/tests/test_browser_automation.py` covering the
      contract (ABC not instantiable, FakeComputer records calls), the
      license guard (raises on injected `cua_som`), the adapter (helpful
      error without cua; delegation with stub cua), and the runner
      (ordered execution, approval halt, allowlist enforcement).
- [ ] 4. GREEN: implement `computer.py`, `license_guard.py`, `runner.py`
      until the suite passes.
- [ ] 5. Run full backend suite + `ruff check` (bar: E4,E7,E9,F,I).
- [ ] 6. Write `docs/reference-architectures/cua-browser-automation.md`.
- [ ] 7. Write `docs/evidence/TW-178-cua-reference-architecture.tdd.md`
      (exact skill format).
- [ ] 8. Pre-review self-scan (secrets/injection/network/subprocess).
- [ ] 9. Commit as Twistor (RED then GREEN checkpoints), push branch.
- [ ] 10. Diff to Rosa for review; update TW-178. DO NOT merge.
