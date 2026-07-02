# Changelog

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
