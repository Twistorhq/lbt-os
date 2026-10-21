# TW-652 Evidence: Biometric Consent Screens

Prepared by Twistor Holdings LLC
Lane: Sofia Reyes, Frontend Engineer. Branch: sofia/tw-652-social-surfaces (rebased onto main at 9707fc6, so the TW-650 contracts are in tree).
Gate that opened this lane: Justynn adopted TW-654 D1 (and D2, D3) on 2026-10-20, unfreezing the consent UX build.

## What was built

The Twistor Social biometric consent flow, mobile-first, day scheme, as React components consuming the foundation's `--soc-*` tokens only:

| File | Contents |
|---|---|
| `frontend/src/components/social/strings.js` | EN and ES catalogs (72 leaf strings each, identical key sets), `BIOMETRIC_POLICY_VERSION` and `RETENTION_SCHEDULE_VERSION` (both 1.0), `getStrings`, `fill` |
| `frontend/src/components/social/social.css` | Component styles on the foundation tokens; imports `design-system/social/tokens.css` |
| `frontend/src/components/social/SocBandChip.jsx` | Pattern 00 chip: exactly three variants (21+ green, 18+ black, TEEN outlined); no restriction variant exists, by design |
| `frontend/src/components/social/SocActionBar.jsx` | Pattern 00 fixed bottom bar: one filled primary, one always-rendered text secondary |
| `frontend/src/components/social/SocScreen.jsx` | Screen shell (step label, Fraunces title, 560px cap, thumb padding) |
| `frontend/src/components/social/consent/ConsentScreens.jsx` | Explain, Schedule, Grant, Declined |
| `frontend/src/components/social/consent/VerificationStates.jsx` | CaptureHandoff, Pending, Approved, Rejected, Dormant |
| `frontend/src/components/social/consent/BiometricConsentFlow.jsx` | Phase orchestrator; emits the consent payload |
| `frontend/src/components/social/__tests__/strings.test.js` | Catalog parity guard (6 tests) |
| `frontend/src/components/social/__tests__/BiometricConsentFlow.test.jsx` | Behavior tests (14 tests) |
| `frontend/scripts/render-consent-preview.mjs` | Server-renders every phase in both languages to the preview HTML |
| `docs/evidence/TW-652-consent-preview.html` | The rendered preview: 20 frames (10 states times 2 languages) |

## Contract mapping (TW-650, merged)

The grant emits exactly the `vfy_consents` fields: `consentType: 'biometric_capture'`, `policyVersion`, `retentionScheduleVersion`, plus `locale` and `grantedAt` for the caller's record. The attestation is an unchecked-by-default checkbox plus a separate affirmative tap; the agree action is disabled until the attestation is checked. Consent is never bundled with terms acceptance, and the grant screen says so in words.

## State coverage (brief item 4)

| State | Where | Notes |
|---|---|---|
| Pre-capture explanation | ExplainScreen | What is captured (ID, live selfie, face template) and the three exclusive purposes from TW-654 Section 1.4 item 1a; door-check framing and the exact badge language |
| Retention schedule | ScheduleScreen | Plain language: photos destroyed at decision, 72-hour ceiling; template life-of-tier in the verify service; closure, withdrawal, and the 24-month dormancy rule; no-sale covenant; link out to the full policy (callback, see gap G5) |
| The grant | GrantScreen | Versioned (policy and retention versions printed on screen and recorded), unbundled, affirmative attestation |
| Decline path | DeclinedScreen | Nothing captured; browse continues; "we will not keep asking"; reachable from explain, schedule, grant, and capture |
| Capture handoff | CaptureHandoff | Frame only: the guided capture is engine-surface work. Carries retakes-are-free guidance and the accessible alternative (human review with assisted capture) as a visible action, per pattern 01's launch requirement |
| Decision pending | PendingState | Names the human reviewer; states no duration (see gap G2); browse continues |
| Approved | ApprovedState | "Checked at the door" banner, band stated as a band with the glance-size chip, never a birthdate |
| Rejected | RejectedState | Both remedies named: guided retake (secondary) and human review (primary, the appeal path surfaced first). No fraud or fake language; a banned-anchor refusal renders identically (TW-646 Section 6.2) |
| Dormancy notice | DormantState | Template destroyed on the published schedule (date rendered when supplied), tier dropped with notice, profile and history intact, re-verify action |

## Tests

Components and tests landed in one pass; the suites are the standing guard, not a staged RED/GREEN pair, and are reported that way honestly.

- New: 20 of 20 pass (14 behavior, 6 parity), run 2026-10-20.
- Full frontend suite: 142 of 142 pass across 17 files. Zero regressions; the change adds files only.
- Behavior assertions are the legal ones: grant disabled until attestation; payload fields exact; decline from two different screens grants nothing and calls no consent callback; pending copy contains no duration claim (`/\d+\s*(hours?|days?|minutes?)/` asserted absent); rejected copy contains neither "fraud" nor "fake".
- Parity assertions: identical EN and ES key sets; every leaf a non-empty string in both locales; Spanish distinct from English on content keys; the 72-hour and 24-month figures present in both locales' schedule and dormancy strings.

## Contrast (WCAG relative luminance, computed 2026-10-20)

Every pair these screens use, computed fresh this run; chip pairs reuse the foundation's verified values.

| Pair | Ratio | Verdict |
|---|---|---|
| Ink on Paper | 16.70 | AAA |
| Ink on Crema (panel) | 15.17 | AAA |
| Muted Clay on Paper | 5.22 | AA |
| Muted Clay on Crema | 4.74 | AA |
| Indigo Deep on Paper | 7.35 | AAA |
| Indigo Deep on Crema | 6.68 | AA |
| Warm White on Indigo Deep (primary action) | 7.03 | AAA |
| Valid green on Paper | 5.32 | AA |
| Warm White on Valid green (approved banner, chip 21+) | 5.09 | AA |
| Valid green on Crema | 4.83 | AA |
| Danger on Paper | 6.14 | AA |
| Danger on Crema | 5.58 | AA |
| Caution on Paper | 5.07 | AA |
| Caution on Crema | 4.61 | AA |
| Warm White on Ink (chip 18+) | 15.97 | AAA |
| Cafecito on Crema (standing text) | 4.98 | AA |

All 16 pairs pass AA or better. Focus ring is the foundation token on every interactive element; touch targets meet the 48px minimum and 56px primary from pattern 00; color is never the only signal (banners pair icon shape with words, the chip is text).

## Impeccable gate (TW-415 standing)

- Detector on the component sources (regex pass): no findings.
- Detector on the rendered preview HTML (parsed pass): 22 findings, all accounted for. 20 are `cramped-padding` against the preview harness's own phone-frame divs (the fixed action bar sits flush at the frame's bottom edge by design; in the product it anchors to the viewport with safe-area inset padding, per pattern 00). 2 are `overused-font` for Inter and Fraunces, the deliberate brand typefaces: Inter is the shared family body font and Fraunces is Social's chosen display voice, both decided in the foundation with reasons. Kept on purpose, documented here, not silently kept.
- Manual 15-tell audit of the components: no gradients, no gradient text, no glassmorphism, no pill badges (chips are squared, 8px radius, text-carrying), no icon-in-rounded-square tiles, no card grids, no cards in cards (the schedule panel groups hairline rows), no side stripes, no hero stats, no decorative animation. The one numbered element, "Step 1 of 4", is a real step count prescribed by pattern 01. The warm paper scheme is the foundation's documented kept-on-purpose palette decision. Copy contains zero em dashes and zero en dashes (grep verified across all new files).

## Gaps flagged for Angela (filed on TW-652; nothing here invents legal copy)

- **G1. No verbatim final consent text exists yet.** The TW-654 package specifies the required elements (Section 1.4, item 1a); these screens render a faithful plain-language structuring of those elements. D1 authorizes outside counsel to review the final consent text; once counsel clears it, the cleared text drops into the catalog and the policy version bumps. Production capture stays behind the D3 flag regardless.
- **G2. The human-review window is unsupplied.** Pattern 01 requires the pending screen to state the expected window; neither the spec nor the legal package gives a figure for verification review (the 24-hour and 7-day figures in the spec govern sanction appeals, a different flow). The pending screen promises notification on decision and states no duration, and a test guards that. Supplying the figure changes one catalog string per language.
- **G3. The Spanish legal rendering needs its human review.** TW-646 Section 3 and pattern 01 require the consent screens' Spanish to be human translated and reviewed. The ES catalog is a full translation at parity, including the badge line rendered as "Verificado en la puerta"; the decided badge language is English, and its Spanish rendering is unconfirmed. Angela and Imani review before launch.
- **G4. The Door Pass photo consent is intentionally not built.** It is consent moment (b), a separate act under TW-647 Section 6.3, behind Angela's TW-647 confirmations and the Door Pass hold. Pattern 01 step 4 does not render until that gate clears.
- **G5. The public biometric policy URL is a callback.** D2 approves the policy for publication before capture; its stable URL is Angela's deliverable. The schedule screen calls `onPolicy`; wiring the URL is a one-line integration once filed.
- **G6. The withdrawal surface lives in another lane.** The schedule screen states the withdrawal right and its effect (template destroyed, account returns to browsing only) per Section 1.4 item 1a, and `vfy_consents.revoked_at` exists in the schema. The Settings surface where a member actually withdraws belongs to the private-corner build (pattern 02, item 4) and is flagged so it does not fall between lanes.

## What this run is not

- No route wiring: the app's router and auth shell are untouched. The flow is a component tree with callback props, ready to mount at the verify entry points (pattern 01) when the verify surface integration is dispatched.
- No capture UI: document and liveness capture are engine surfaces (TW-646 Section 5.2); this build frames the handoff and the exits.
- Nothing merged. Rosa reviews through the normal gate; Justynn's word merges.

Prepared by Twistor Holdings LLC
