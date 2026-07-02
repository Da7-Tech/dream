---
name: dream
description: Sleep-cycle cleanup for agent memory files, budget-safe.
version: 1.2.0
author: Da7-Tech
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  commands: [python3, curl]
related_skills: [hermes-agent]
metadata:
  hermes:
    tags: [Memory, Consolidation, Hygiene, Offline, Deterministic]
    category: autonomous-ai-agents
    homepage: https://github.com/Da7-Tech/dream
---

# dream Skill

Consolidates the agent's own memory files (`~/.hermes/memories/MEMORY.md`,
`USER.md`) offline and deterministically: deduplicates, keeps the newest
statement of a repeated subject, flags uncertain contradictions, and can
squeeze the file under its exact character budget — with zero LLM calls
(the char accounting matches Hermes' `§`-join math character-for-character — the same `len()` codepoint count Hermes uses). It does
NOT rewrite prose the way a model would, and it never deletes: every
removed entry is archived with a written reason.

## When to Use

- The user asks to clean up, consolidate, or "run a dream on" their memory
- The built-in memory is at capacity and consolidation keeps failing
- Scheduled memory hygiene
- Any agent memory file: `--format bullets` handles Claude-Code-style
  bullet memories, `paragraphs` handles free notes

## Prerequisites

- `python3` (3.9+) and `curl` on PATH — no API keys, no server, no
  packages. The tool is one stdlib-only file, MIT-licensed, from
  https://github.com/Da7-Tech/dream (46 tests + a 90-day soak test run in
  its CI on Linux/macOS/Windows).

## How to Run

Install once through the `terminal` tool, pinned to a release tag and
integrity-checked:

```bash
mkdir -p ~/.hermes/tools && cd ~/.hermes/tools
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/dream/v1.2.0/dream.py
echo "1f94b3418fc05a71791793f13ab33837974ecfb2361ffe24906afeb115ef4704  dream.py" | shasum -a 256 -c
```

## Quick Reference

| User intent | Command (through `terminal`) |
|---|---|
| "Clean up my memory" | `python3 ~/.hermes/tools/dream.py --hermes` (dry run) |
| Apply after approval | add `--apply` |
| "Memory is over its limit" | `python3 ~/.hermes/tools/dream.py --hermes --apply` (budgets auto-read from `config.yaml`) |
| Consolidate any notes file | `python3 ~/.hermes/tools/dream.py <file>` then `--apply` |

## Procedure

1. **Always dry-run first** and show the user the plan: the dry run prints
   the journal (every action + reason) and a unified diff, writing nothing.
2. Apply only on approval. `--apply` writes a timestamped backup, moves
   every removed entry to `<file>.dream-archive.md` with the reason, and
   appends a report to `DREAMS.md`.
3. Report `entries: N -> M | chars: X -> Y` plus backup/archive paths.
   Never say an entry was "deleted" — it was archived.
4. Nightly automation costs zero tokens via the `cronjob` tool in no-agent
   mode (the script IS the job — no model call):

```bash
cat > ~/.hermes/scripts/dream_nightly.sh <<'EOF'
#!/bin/bash
python3 ~/.hermes/tools/dream.py --hermes --apply --quiet
EOF
hermes cron create "0 4 * * *" --name memory-dream --script dream_nightly.sh --no-agent
```

## Pitfalls

- Supersession assumes file order = recency (true for Hermes' append-style
  memory). For non-chronological files use `--no-supersede`.
- Merging is deterministic clause algebra, not LLM rewriting — wording can
  be mechanical; information is preserved.
- Pairwise comparison is O(n²): instant for memory-sized files, not meant
  for megabyte corpora.
- Apply is idempotent (unit-tested): a second run on clean memory changes
  nothing.

## Verification

```bash
cd "$(mktemp -d)" && curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/dream/v1.2.0/dream.py && printf 'user name is raif\n§\nuser name is raif' > MEMORY.md && python3 dream.py MEMORY.md --apply --quiet && cat MEMORY.md
```

Expected: `MEMORY.md consolidated: 2 -> 1 entries, ...` and the file
contains the fact once; the duplicate is in `MEMORY.md.dream-archive.md`.
