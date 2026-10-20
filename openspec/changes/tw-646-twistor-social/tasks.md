<!-- Prepared by Twistor Holdings LLC -->

# TW-646: Twistor Social: tasks

Sequenced per architecture spec Section 13. Each phase is its own ticket,
its own branch and worktree, RED-first tests, a TDD evidence report in
`docs/evidence/`, Rosa review before merge, and Justynn's merge word per
merge. A phase does not start until its gates clear.

## Phase 0: This package (TW-648, Zeke)

- [x] 1. Author proposal.md, design.md, tasks.md, .openspec.yaml under
      `openspec/changes/tw-646-twistor-social/` from the three source specs.
- [x] 2. Validate the package (openspec validate: "Change
      'tw-646-twistor-social' is valid", run 2026-10-20 on the branch).
- [ ] 3. Self-scan the package (no secrets, no embedded instructions, no
      scope beyond the source specs); commit on
      `zeke/tw-648-social-openspec`. Do NOT merge.
- [x] 3a. Fold in Justynn's 2026-10-20 Section 12 decisions: ambassador
      review staffing, home hosting with close friends and audience
      scoping, NO vendor fallback (bridge language removed), display
      rules; plus the TW-654 retention change-list item (HB 24-1130).
- [ ] 4. Rosa reviews proposal and design. Build phases below proceed only
      after her approval.

## Phase 1: Verification engine benchmark (TW-649, Zeke, parallel with Phase 0)

- [ ] 5. Benchmark harness skeleton in `backend/app/verify/benchmark/`:
      metric definitions as code (FMR, FNMR, per-group breakdown, demographic
      gap, gate evaluation with INSUFFICIENT_DATA state; OCR field accuracy;
      MRZ check digit; PDF417 agreement; APCER/BPCER), dataset registry
      with license and consent notes per dataset, stage adapter interfaces
      with lazy imports (heavy model deps never in the default path).
- [ ] 6. RED-first tests for the metric computations; GREEN implementation;
      evidence report `docs/evidence/TW-649-verify-benchmark.tdd.md`.
- [ ] 7. First smoke run on deterministic synthetic scores, output committed
      as an example report. Smoke data is synthetic scores, never presented
      as measured accuracy.
- [ ] 8. Real benchmark run on registered datasets once adapters land;
      launch evidence report with per-group numbers; gate evaluation.
      GATE: T2 does not open on a FAIL or INSUFFICIENT_DATA result.

## Phase 2: Verification engine build (ticket on Rosa's approval of Phase 0)

- [ ] 9. `vfy_` migrations (sessions, documents, biometrics, signals,
      identity anchors, age attestations, consents, engine versions), RLS
      deny-all, idempotent (with TW-650).
- [ ] 10. Pipeline stages 1 to 5 with human review queue; verdict-only API
      per design; images destroyed on decision, 72-hour ceiling.
- [ ] 10a. Retention schedule alignment (TW-654 change list; Rosa
      verifies against counsel's text): destruction jobs implement the
      corrected Section 6.3 schedule, including Colorado HB 24-1130
      24-month dormancy destruction for biometric identifiers (face
      templates, door photos) with logged counsel-confirmed exceptions;
      the published retention schedule states the rule before any
      production capture.
- [ ] 11. Identity verification pointer migration and write-scope minting
      from the pointer.
- [ ] 12. GATE: Angela consent and retention sign-off (TW-654) before any
      production capture. Benchmark and staging work proceed without it;
      production faces do not move.

## Phase 3: Schema and service skeletons (TW-650, Dre)

- [ ] 13. `rsn_` migrations per spec Section 4 (profiles, couple links,
      groups, memberships, blocks, events, locations, series, RSVPs, guest
      passes, reveals, check-ins, encounter confirms, welfare checks,
      reviews, trusted contacts, trust ledger and scores, collectible rules
      and instances, vouches, posts, reactions, comments, voice usage,
      reports, bans, sanctions, appeals, evidence packets, disclosures,
      audit log, plan catalog, organizer plans), RLS deny-all.
- [ ] 14. `backend/app/social/` skeletons (models, service, router, safety,
      trust, sessions, geo) with fail-loud Pydantic models and rate limits.

## Phase 4: Social core features (per spec Section 13 step 4)

- [ ] 15. Groups, memberships, roles, group tier floor; profiles; couple
      links with per-person privacy; blocks as mutual invisibility.
- [ ] 16. Events and series (RRULE, rolling 4-week materialization), RSVPs
      with standing-tier waves and waitlist, tiered location reveal with
      logging and watermark.
- [ ] 17. Ghost town enforcement: read-only session scopes for unverified
      accounts, entitlement-layer refusal of every write, closed skeleton
      surfaces only.
- [ ] 17a. Close friends and audience scoping: `rsn_close_friends`
      (owner-curated); audience scope on posts and events (group,
      connections, close_friends) enforced at the query layer alongside
      Section 5.9 filtering.
- [ ] 17b. Home hosting: home venue type on events; exact address
      bronze-only, never on the public index or any anonymous surface;
      reveal restricted to the host's close friends plus host-approved
      confirmed guests inside the standard window, every reveal logged;
      full check-in loop applies unchanged.
- [ ] 18. Age-gated visibility at the query layer on every surface;
      fail-closed unrated handling; restricted shell for shared links and
      generic OG previews for gated targets.

## Phase 5: Check-in loop, trust, rewards

- [ ] 19. Geo arrival (on-device verdict), venue QR strong check-in,
      encounter confirms with identity-strike handling.
- [ ] 20. Welfare check with help sheet, trusted contacts, silent alert,
      discreet mode.
- [ ] 21. Post-event write-ups; trust ledger projections (completed night,
      show rate, would-meet-again, hosted-clean, streaks); band and standing
      tier computation with dormancy step-down and band suspension per
      design; Notify workflows for the whole loop (en + es).
- [ ] 22. Guest passes end to end (attach rules, host-confirm arrival,
      anchor ledger writes, suspension arithmetic, conversion invite).
- [ ] 23. Collectibles derivation job and revocation cascade; recognition
      celebrations, standings, Year Out, standing card.

## Phase 6: Safety system

- [ ] 24. Reports with SLA timers and weighted triage; staff safety console
      (desktop operator layer).
- [ ] 25. Sanctions ladder and appeals workflow with SLAs; identity-anchored
      bans with anchor-lookup refusal at T2; temporary safety suspensions.
- [ ] 26. Evidence packets (hash-chained manifests), disclosure log, audit
      log; integrity lifecycle: duplicate sweeps, liveness re-challenges,
      bot-signal cases, purge pipeline, dormant-ghost sweep.
- [ ] 26a. Ambassador program: `rsn_ambassadors` role with queue-scoped
      entitlement grants; invitation flow via Notify (in-app + email) to
      Pillar/Keyholder members in good standing; Angela-vetted onboarding
      (agreement + training) before queue access; verification exception
      and report triage queues with bounded decisions (no sanctions, no
      evidence, no appeals; serious categories bypass to staff at
      intake); instant staff revocation with case reassignment and
      automatic suspension on standing drop or substantiated report.
- [ ] 27. Media ingest choke point (re-encode, EXIF and GPS strip) wired
      into every upload path; privacy controls pass (attendance toggle,
      couple-link privacy, profile visibility, blocks absolute).

## Phase 7: Events data layer (TW-651, Dre; runs in the Phase 4 to 5 window)

- [ ] 28. `evt_` migrations (sources, raw items, venues, listings, listing
      sources, claims); geocoding provider interface with venue cache and
      rate-limited Nominatim queue.
- [ ] 29. ICS poller, Eventbrite-class API client, organizer OAuth import;
      fingerprint dedupe; freshness and cancellation handling; fail-closed
      provenance gate and publish log.
- [ ] 30. Public index surface (TW-604 pattern): metro, category,
      neighborhood, and per-listing pages with schema.org Event JSON-LD,
      Leaflet and OSM previews, directions deep links; member-side
      Section 5.9 enforcement inside the product.
- [ ] 31. Claim-a-listing (OAuth, domain, staff review ladder), rating
      confirmation at claim, group conversion, claim toll.

## Phase 8: Organizer billing

- [ ] 32. Plan catalog (prices NULL, Justynn sets at launch), organizer
      plans, entitlement checks; toll kinds `social.organizer_plan_period`,
      `social.event_promoted`, `social.listing_claim`, idempotency-keyed,
      entitlement first and toll second.

## Phase 9: Surfaces (TW-652, Sofia; parallel from Phase 4)

- [ ] 33. Member PWA, mobile-first: verification flow, ghost town skeleton,
      directory and landing shells, groups, events, RSVP, check-in loop
      screens, feed, standing and rewards surfaces, scan history.
- [ ] 33a. Display rules on every member surface: standing tier visible
      on profiles; sanction and restriction state never rendered anywhere
      member-facing; age band as a color chip (21 plus green, 18 to 20
      black, teen flagged "teen"), band only, never a birthdate.
- [ ] 34. Operator desktops: organizer console, staff safety console; venue
      dashboard lands with Phase 10.
- [ ] 35. String catalog complete for en + es with reviewed human
      translation; iOS home-screen web-app push evaluation for the welfare
      loop, reported with data.

## Phase 10: Door Pass (TW-653, Zeke; after Phases 2 to 6 dependencies exist)

- [ ] 36. `dps_` migrations (venues, locations, lanes, staff, plans, device
      keys, nonces, pass state, scans); `backend/app/door/` minting with
      device-key challenge; nonce registry with expiry reaping.
- [ ] 37. Verify endpoint (cheapest-first validation, live-state checks,
      three result states, per-scan signed photo URL) and result page.
- [ ] 38. Door photo capture behind its own Angela consent gate (disabled
      flag until cleared); door mode; scan history with dispute flow.
- [ ] 39. Venue claim flow, lanes, signage kit behind its copy gate; scan
      tolls `doorpass.scan` keyed `doorpass:{jti}`; bundle counter with
      80 percent warnings; lane dashboard (aggregates only).
- [ ] 40. GATE: venue pilot only after the TW-647 Section 9 gates that
      touch live doors clear (liquor-law position, venue contract terms,
      signage claims).

## Phase 11: Launch (Justynn's calls, spec Sections 9 and 12)

- [ ] 41. Soft launch: 500 T2-verified members, 10 seeded groups, public
      venues plus close-friends-scoped home events, ambassador program
      recruited and vetted so the review SLA is staffed, benchmark gate
      passed, Angela launch items cleared.
- [ ] 42. Public Denver launch on the Section 9 numbers (2,500
      verified-active, fill rate at or above 70 percent in soft launch).
- [ ] 43. `openspec archive` folds the outcome back and closes TW-646's
      build tickets.
