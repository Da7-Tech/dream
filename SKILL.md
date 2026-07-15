---
name: dream
description: Sleep-cycle cleanup for agent memory files, budget-safe.
version: 1.4.0
author: Da7_Tech
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

Consolidates the active Hermes profile's `MEMORY.md` and `USER.md` offline
and deterministically: deduplicates, keeps the newest
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
  https://github.com/Da7-Tech/dream (72 tests + a 90-day soak test run in
  its CI on Linux/macOS/Windows).

## How to Run

Install once through the `terminal` tool, pinned to a release tag and
integrity-checked:

POSIX shell (Linux/macOS):

```bash
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_ROOT/tools"
cd "$HERMES_ROOT/tools"
curl -fsSLo dream.py https://raw.githubusercontent.com/Da7-Tech/dream/v1.4.0/dream.py
python3 -c "import hashlib;h=hashlib.sha256(open('dream.py','rb').read()).hexdigest();assert h=='616997cb6caa4de403e34a1153fe9d15ec8196246039393946d1cb2fb06aa052',h;print('dream.py: OK')"
```

PowerShell (Windows):

```powershell
$HermesRoot = if ($env:HERMES_HOME) {
  $env:HERMES_HOME
} else {
  Join-Path $env:LOCALAPPDATA "hermes"
}
$Tools = Join-Path $HermesRoot "tools"
New-Item -ItemType Directory -Force $Tools | Out-Null
$Dream = Join-Path $Tools "dream.py"
Invoke-WebRequest "https://raw.githubusercontent.com/Da7-Tech/dream/v1.4.0/dream.py" -OutFile $Dream
$Hash = (Get-FileHash $Dream -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Hash -ne "616997cb6caa4de403e34a1153fe9d15ec8196246039393946d1cb2fb06aa052") { throw "dream.py checksum mismatch: $Hash" }
```

## Quick Reference

| User intent | Command (through `terminal`) |
|---|---|
| "Clean up my memory" | run the installed `dream.py --hermes` (dry run) |
| Apply after approval | add `--apply` |
| "Memory is over its limit" | run `dream.py --hermes --apply` (budgets auto-read from the active profile's `config.yaml`) |
| Consolidate any notes file | run `dream.py <file>` then add `--apply` |

## Procedure

1. **Always dry-run first** and show the user the plan: the dry run prints
   the journal (every action + reason) and a unified diff, writing nothing.
2. Apply only on approval. `--apply` writes a timestamped backup, moves
   every removed entry to `<file>.dream-archive.md` with the reason, and
   appends a report to `DREAMS.md`.
3. Report `entries: N -> M | chars: X -> Y` plus backup/archive paths.
   Never say an entry was "deleted" — it was archived.
4. Nightly automation costs zero tokens. Use the Hermes `cronjob` tool on
   POSIX; use Windows Task Scheduler on native Windows.

POSIX only:

```bash
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_ROOT/scripts"
cat > "$HERMES_ROOT/scripts/dream_nightly.sh" <<'EOF'
#!/bin/bash
set -eu
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"
python3 "$HERMES_ROOT/tools/dream.py" --hermes --apply --quiet
EOF
hermes cron create "0 4 * * *" --name memory-dream --script dream_nightly.sh --no-agent
```

Native Windows:

```powershell
$HermesRoot = if ($env:HERMES_HOME) {
  $env:HERMES_HOME
} else {
  Join-Path $env:LOCALAPPDATA "hermes"
}
$Python = (Get-Command python).Source
$Dream = Join-Path $HermesRoot "tools\dream.py"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Dream`" --hermes --apply --quiet"
$Trigger = New-ScheduledTaskTrigger -Daily -At 4am
Register-ScheduledTask -TaskName "Hermes memory dream" -Action $Action -Trigger $Trigger
```

## Pitfalls

- Supersession assumes file order = recency (true for Hermes' append-style
  memory). For non-chronological files use `--no-supersede`.
- Merging is deterministic clause algebra, not LLM rewriting — wording can
  be mechanical; information is preserved.
- Pairwise comparison is O(n²), with hard ceilings: 10 MB, 10,000 entries,
  and 200,000 comparisons. A limit failure writes no semantic change.
- Apply is idempotent (unit-tested): a second run on clean memory changes
  nothing.
- `HERMES_HOME` always selects the profile. Without it, the native defaults
  are `~/.hermes` on POSIX and `%LOCALAPPDATA%/hermes` on Windows.

## Verification

POSIX:

```bash
tmp="$(mktemp -d)"
cd "$tmp"
curl -fsSLo dream.py https://raw.githubusercontent.com/Da7-Tech/dream/v1.4.0/dream.py
python3 -c "import hashlib;h=hashlib.sha256(open('dream.py','rb').read()).hexdigest();assert h=='616997cb6caa4de403e34a1153fe9d15ec8196246039393946d1cb2fb06aa052',h;print('OK')"
printf 'user name is khalid\n§\nuser name is khalid' > MEMORY.md
python3 dream.py MEMORY.md --apply --quiet
cat MEMORY.md
```

PowerShell:

```powershell
$Tmp = Join-Path $env:TEMP ("dream-" + [guid]::NewGuid())
New-Item -ItemType Directory $Tmp | Out-Null
Set-Location $Tmp
Invoke-WebRequest "https://raw.githubusercontent.com/Da7-Tech/dream/v1.4.0/dream.py" -OutFile dream.py
if ((Get-FileHash dream.py -Algorithm SHA256).Hash.ToLowerInvariant() -ne "616997cb6caa4de403e34a1153fe9d15ec8196246039393946d1cb2fb06aa052") { throw "checksum mismatch" }
[IO.File]::WriteAllText((Join-Path $Tmp "MEMORY.md"), "user name is khalid`n§`nuser name is khalid")
python dream.py MEMORY.md --apply --quiet
Get-Content MEMORY.md
```

Expected: `MEMORY.md consolidated: 2 -> 1 entries, ...` and the file
contains the fact once; the duplicate is in `MEMORY.md.dream-archive.md`.
