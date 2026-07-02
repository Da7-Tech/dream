# Changelog

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
