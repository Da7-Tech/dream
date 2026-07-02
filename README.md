# dream — a sleep cycle for your agent's existing memory

[![tests](https://github.com/Da7-Tech/dream/actions/workflows/ci.yml/badge.svg)](https://github.com/Da7-Tech/dream/actions/workflows/ci.yml)
[![deps](https://img.shields.io/badge/dependencies-0-brightgreen)](https://github.com/Da7-Tech/dream/blob/main/dream.py)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![English](https://img.shields.io/badge/README-English-2ea44f)](README.md)
[![العربية](https://img.shields.io/badge/README-%EF%BA%8D%EF%BB%9F%EF%BB%8C%EF%BA%AE%EF%BA%91%EF%BB%B4%EF%BA%94-8A2BE2)](README.ar.md)

**One Python file. Zero dependencies. Zero LLM calls. 100% deterministic. Never deletes — only archives.**

Your coding agent already has a memory file — Hermes' `MEMORY.md`, Claude
Code's memory, a notes section in `AGENTS.md`. That file only ever grows:
duplicates pile up, corrected facts sit next to the stale ones they
corrected, and hard character budgets (Hermes: **2,200 chars**) overflow.

`dream` gives that file what a brain gets every night — a sleep cycle:

```
light sleep   exact + near-duplicate detection (keeps the richest wording)
deep sleep    supersession: same subject stated twice → the newer wins,
              the older is archived with a written reason
REM           recurring-theme report + contradiction flags (report only)
squeeze       --budget N: merge related entries clause-safely, then archive
              the most redundant ones until the file fits its hard limit
```

Unlike every other memory consolidator (Letta sleep-time compute, OpenClaw
dreaming, mem0), it never calls an LLM: consolidation is pure deterministic
text algebra — **zero tokens, zero keys, runs offline, same input → same
plan, every time** — which also means you can put it in cron and forget it.

## Quick start

```bash
curl -O https://raw.githubusercontent.com/Da7-Tech/dream/main/dream.py

python3 dream.py MEMORY.md              # dry run: journal + diff, no writes
python3 dream.py MEMORY.md --apply      # consolidate (backup + archive + journal)
python3 dream.py --hermes --apply       # Hermes: ~/.hermes/memories/* with the
                                        # char budgets read from config.yaml
```

Dry run is the default. `--apply` always writes a timestamped backup, moves
every removed entry into `<file>.dream-archive.md` **with the reason**, and
appends a human-readable report to `DREAMS.md`.

## What it looks like on a real memory

Run against a real, lived-in Hermes `MEMORY.md` (13 entries, 5,926 chars,
mixed Arabic/English), the dry run found exactly two consolidations and
zero false positives:

```
- [deep] superseded — same subject stated again later in the file
    archived: <a claim about a provider endpoint from Jul 1>
    kept/now: "Correction (Jul 1): <the agent's own later correction
              of that exact claim>"
- [deep] superseded — same subject stated again (100% subject overlap)
    archived: <feature note, older wording>
    kept/now: <same feature note, richer wording with the config path>

entries: 13 -> 11 | chars: 5926 -> 5066
```

Both actions are exactly right: the first entry had literally been
corrected by a later "Correction:" entry the agent appended — `dream`
noticed, kept the correction, archived the stale claim.

## Formats — works with any agent's memory

Auto-detected (or force with `--format`):

| format | used by | entries are |
|---|---|---|
| `sections` | **Hermes** (`MEMORY.md`, `USER.md`) | blocks separated by `§` — size accounting matches Hermes' own char-limit math exactly |
| `bullets` | Claude Code memory, most `AGENTS.md` notes | `- ` bullets (with continuation lines); headers preserved untouched |
| `paragraphs` | plain notes files | blank-line separated blocks |

Bilingual: tokenization and stemming handle English **and Arabic** (with
broken-plural handling), so mixed-language memories consolidate correctly.

## Safety model (the whole point)

1. **Dry run by default** — you always see the diff and the reasons first.
2. **Nothing is ever destroyed** — every removed entry goes to
   `<file>.dream-archive.md` with a timestamp and the reason.
3. **Timestamped backup** of the original before every `--apply`.
4. **Uncertain cases are flagged, not acted on** — low-overlap conflicts go
   to the journal as flags for you (or your agent) to resolve.
5. **Atomic, symlink-refusing writes**; idempotent (a second dream on a
   clean memory changes nothing — this is a unit test).

## Options

```
--apply          write changes (default: dry-run preview)
--budget N       enforce a hard character budget (Hermes MEMORY.md: 2200)
--format F       auto | sections | bullets | paragraphs
--max-age D      also archive entries first seen more than D days ago
                 (age is tracked in a sidecar state file across runs)
--no-supersede   dedup only
--no-merge       never merge; archive-only squeeze
--quiet          one summary line
```

## Measured

- 40 unit tests, stdlib `unittest`: `python3 -m unittest discover -s tests`
- **90-day soak in CI** (`bench/soak.py` — real code, injected clock, daily
  churn + nightly budgeted dream): budget held on all 90 nights, newest
  statement of every evolving subject won, nothing lost (file ∪ archive),
  byte-identical across full reruns
- Determinism and idempotence are tested, not promised
- Consolidating a 13-entry real-world memory: **< 20 ms**, 0 tokens

## When you want more than consolidation

`dream` improves the memory your agent already has. If you want a full
brain-like memory — spreading-activation recall, Ebbinghaus forgetting,
cross-agent export — that's the sister project:
[**mind**](https://github.com/Da7-Tech/mind). Use either, or both:
`mind` for project memory, `dream` for the agent's own memory file.

## Honest limitations

- Supersession trusts file order (memory files are append logs; later =
  newer). If yours isn't append-ordered, use `--no-supersede`.
- Merging is clause-level text algebra: it preserves every novel clause but
  won't rewrite prose the way an LLM would — by design (determinism > polish).
- Theme detection is term-frequency based, a report not an oracle.
- Pairwise comparison is O(n²): instant for agent-memory files (a 13-entry
  real memory: ~2 ms), slow for huge files (an adversarial 18,000-entry,
  1 MB file took ~3 minutes). It is a memory-file tool, not a corpus tool.

Arabic README: [README.ar.md](README.ar.md) · License: MIT
