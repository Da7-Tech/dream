---
name: dream
description: >-
  Deterministic sleep-cycle consolidation for the agent's own memory files:
  dedup, supersession, contradiction flags, and hard char-budget squeeze for
  MEMORY.md/USER.md. Zero LLM calls, zero tokens, dry-run first, never
  deletes (archives with reasons). One Python file, zero dependencies.
version: 1.0.0
author: Da7-Tech
license: MIT
platforms: [linux, macos]
prerequisites:
  commands: [python3, curl]
metadata:
  hermes:
    tags: [Memory, Consolidation, Hygiene, Offline, Deterministic]
    requires_toolsets: [terminal]
    category: autonomous-ai-agents
    homepage: https://github.com/Da7-Tech/dream
---

# dream — Sleep-Cycle Consolidation for Hermes Memory

Hermes' built-in memory (`~/.hermes/memories/MEMORY.md`, `USER.md`) is an
append-style curated file with a hard character budget (default 2,200 /
1,375 chars). Over weeks it accumulates near-duplicates and stale facts that
later entries have corrected, and consolidating at capacity costs model
turns. `dream` fixes this offline: a deterministic sleep cycle that
deduplicates, keeps the newest statement of a repeated subject, flags
uncertain contradictions, and can squeeze the file under its exact budget —
**without a single LLM call** (zero tokens; the char accounting matches
Hermes' own `§`-join math exactly).

This skill wraps the standalone open-source tool
(https://github.com/Da7-Tech/dream — one stdlib-only Python file). All
commands below are real and verified.

## When to Use

- The user asks to clean up, consolidate, or "run a dream on" their memory
- The memory tool reports it is at capacity and consolidation keeps failing
- Scheduled memory hygiene (cron)
- Any agent memory file, not just Hermes: `--format bullets` handles
  Claude-Code-style bullet memories, `paragraphs` handles free notes

## Setup (once)

```bash
mkdir -p ~/.hermes/tools && cd ~/.hermes/tools
curl -fsSLO https://raw.githubusercontent.com/Da7-Tech/dream/main/dream.py
```

## Quick Reference

| User intent | Action |
|---|---|
| "Clean up my memory" | `python3 ~/.hermes/tools/dream.py --hermes` (dry run), show the plan + diff, then add `--apply` if approved |
| "Consolidate this notes file" | `python3 ~/.hermes/tools/dream.py <file>` then `--apply` on approval |
| "Memory is over its limit" | `python3 ~/.hermes/tools/dream.py --hermes --apply` (budgets are read from `config.yaml` automatically) |
| Nightly hygiene | see "Nightly cron" below (`--script` + `--no-agent`: zero LLM involvement) |

**Always dry-run first and show the user the plan** unless they explicitly
asked to apply. The dry run prints the journal (every action + reason) and a
unified diff, and writes nothing.

## Nightly cron (truly zero tokens)

Use Hermes' no-agent cron mode so the LLM is never invoked:

```bash
cat > ~/.hermes/scripts/dream_nightly.sh <<'EOF'
#!/bin/bash
python3 ~/.hermes/tools/dream.py --hermes --apply --quiet
EOF
hermes cron create "0 4 * * *" --name memory-dream \
  --script dream_nightly.sh --no-agent
```

`--no-agent` means the script IS the job: its stdout is delivered directly,
no model call happens.

## What each phase does

- **light sleep** — exact and near-duplicate entries merged (richest wording kept)
- **deep sleep** — supersession: same subject stated again later in the file
  → newer wins, older is archived with the reason; low-confidence conflicts
  are only *flagged* in the journal for the user to resolve
- **REM** — recurring-theme report in the journal
- **squeeze** — only with a budget: clause-preserving merges, then the most
  redundant entries are archived until the file fits

## Safety guarantees (tell the user these when asked)

- Nothing is ever destroyed: removed entries go to
  `MEMORY.md.dream-archive.md` with timestamp + reason
- A timestamped backup of the original is written before every `--apply`
- Apply is idempotent: a second run on clean memory changes nothing
- The journal `DREAMS.md` (next to the memory file) explains every decision

## Response style

- After a dry run: summarize actions in one line each (kind + reason), then
  ask whether to apply.
- After apply: report `entries: N -> M | chars: X -> Y` plus backup/archive
  paths. Never claim an entry was "deleted" — it was archived.

## Honest limits

- Supersession assumes file order = recency (true for Hermes' append-style
  memory). For non-chronological files use `--no-supersede`.
- Merging is deterministic clause algebra, not LLM rewriting — wording can
  be mechanical; information is preserved.
