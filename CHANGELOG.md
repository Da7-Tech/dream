# Changelog

## 1.3.0 — 2026-07-05

Tokenizer/stemmer parity with `mind`. `dream` and `mind` share a
tokenizer/stemmer family; `mind` advanced it and `dream` had fallen behind.
Consolidation quality is decided entirely by how entries tokenize, so this
directly widens what `dream` can dedup and supersede — with no change to any
existing behavior (all prior tests unchanged, 53 tests):

- **Arabic broken plurals now unify.** A seed dictionary maps a singular and
  its broken plural onto one stem (قاعدة≡قواعد, وظيفة≡وظائف, كلمة≡كلمات,
  ملف≡ملفات, …), and the lookup runs on the FULL word *before* prefix
  stripping — previously stripping a leading root letter as if it were a
  prefix (كلمة → لمة) bypassed the dictionary entirely. Before this, a
  singular and its broken plural stemmed to *different* tokens, so a fact
  restated with the other number had only partial token overlap. Example:
  «الوكلاء يراجعون الملفات» vs «الوكيل يراجع الملف» stemmed to a 50% overlap
  (below the 0.85 near-duplicate gate) and was caught, if at all, only by the
  looser supersession path; now the two tokenize identically and are
  recognized as near-duplicates in light sleep (the richer wording kept).
  More broadly, unifying singular↔plural raises token overlap everywhere
  jaccard is used — dedup, budget-merge clustering, and the conflict flag.
- **CJK / kana / Hangul / Thai memories now consolidate.** These scripts put
  no spaces between meaning units, so the old tokenizer collapsed a whole
  sentence into a single opaque token that only matched an identical run —
  Chinese/Japanese duplicates never merged. They are now indexed as
  character bigrams (the standard search-engine technique), so near-duplicate
  and richer-restatement detection works across those scripts. Guarded
  against over-merging: genuinely distinct CJK facts still survive.
- **Stopword set aligned with `mind`** (is/be/been/being/does/did/will/its/
  it/my/your/their), so English token overlap is measured on content words.

## 1.2.1 — 2026-07-02

Follow-up to 1.2.0 (two edge cases caught by a verification pass):

- `is_header` is now gated on `bullets` format only. In `sections`
  (Hermes) and `paragraphs`, a leading `#` is ordinary entry content and
  must still dedup/supersede/age normally — 1.2.0 wrongly exempted it.
- `--apply` preflight now also rejects a side-file path that is a
  *directory* (e.g. a `mkdir`'d DREAMS.md), which previously slipped past
  the writability check and half-completed. 48 tests.

## 1.2.0 — 2026-07-02

Second adversarial audit (Opus-4.8 fleet, each finding reproduced-or-refuted).
Confirmed defects fixed, each with a regression test (46 tests):

- **`--apply` is now all-or-nothing.** It preflights the archive/journal/state
  paths before overwriting the live memory; if any is a symlink or unwritable
  it aborts cleanly (exit 1, clear message) instead of half-completing —
  previously it overwrote the target, then died with a raw traceback and the
  removed entries were never archived.
- **Word-order-blind dedup fixed.** "A calls B" and "B calls A" (same token
  bag, reversed order) were archived as "same fact worded twice". Near-dups
  now require the shared tokens in the same order; order-reversed pairs fall
  through to the conflict flag. The equal-length tie-break now keeps the
  LATER (newer) entry, consistent with newest-wins supersession.
- **Bullets parser no longer swallows headers.** A `## header` between bullet
  groups was absorbed as a continuation line and could be deleted as dedup
  side-cargo. Headers are now structural: never deduped, superseded, merged,
  aged-out, or archived-for-budget, and re-emitted in place.
- **`--format` as the last argument** no longer crashes with an IndexError —
  same bounds guard as `--budget`/`--max-age`.
- **Honest measurement:** the 90-day soak's budget assertion was vacuous (the
  workload never exceeded the budget). A dedicated budget-stress leg now feeds
  non-dedupable churn against a small budget so the squeeze path actually
  fires and the hard limit is verified held. Docs corrected: char accounting
  is character/codepoint-based (not "byte-for-byte").

## 1.1.1 — 2026-07-02

Audit-hardening (findings from independent code audits, verified then fixed):

- fsync before rename in atomic writes — power-loss durability was
  previously implied but not guaranteed; O_NOFOLLOW made portable.
- Source guard test: `_now()` is enforced as the only time source.
- CI: actions pinned by commit SHA, least-privilege permissions,
  Windows added to the test matrix (40 tests).

## 1.1.0 — 2026-07-02

- **90-day soak test** (`bench/soak.py`, now in CI): the real code driven
  through an injectable clock (`_now`) over 90 simulated days of daily
  churn + nightly `--apply --budget 2200`. Verified: the budget held on
  all 90 nights, the newest statement of every evolving subject won,
  nothing was ever lost (file ∪ archive conservation), the aged file is
  idempotent, and two full reruns are byte-identical.
- `_now()` injectable clock (refactor, no behavior change).

## 1.0.0 — 2026-07-02

First public release.

- Sleep phases: light (exact/near/containment dedup) → deep (supersession
  with file-order-as-recency, uncertain conflicts flagged only) → REM
  (recurring-theme report) → squeeze (clause-preserving merge, redundancy
  archiving, tail-clause trimming — hard budget guaranteed).
- Formats: `sections` (Hermes `§` delimiter, char accounting identical to
  Hermes' memory tool), `bullets`, `paragraphs`; auto-detected.
- Safety: dry-run default with unified diff, timestamped backups, append-only
  reason-annotated archive, `DREAMS.md` journal (rotated at 64 KB), sidecar
  state for cross-run age tracking, atomic symlink-refusing writes,
  idempotent apply (unit-tested).
- Bilingual EN/AR tokenization with light stemming.
- 33 unit tests (stdlib only). Validated on a real lived-in Hermes memory:
  caught a stale claim that a later "Correction:" entry had superseded, and
  a near-duplicate pair — zero false positives.
