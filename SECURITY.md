# Security Policy

## Supported versions

Only the latest release is supported. `dream.py` is a single stdlib-only
file — updating is replacing one file.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability), or open an issue for non-sensitive
hardening suggestions. You can expect an initial response within a few days.

## Security properties (and their tests)

- One bounded portable lock covers each fresh read, consolidation decision,
  and commit. A durable pending transaction makes interrupted applies
  recoverable and idempotent. The backup and archive are durable before the
  live memory is replaced.
- Atomic local-filesystem writes preserve existing permissions, use
  unpredictable exclusive temporary files, check every byte, `fsync` before
  replace, and `fsync` the destination directory on POSIX. Windows read and
  replace sharing violations are retried.
- Targets and side files require private regular single-link files.
  Symlinks, symlinked parents, hard links, FIFOs, devices, sockets, and
  directories are refused. POSIX operations traverse opened directory
  handles to close parent-swap races.
- A rewrite captures and rechecks file identity so an external edit is never
  silently overwritten by a stale consolidation decision.
- Input, side-file, entry-count, and pair-work ceilings bound memory and
  quadratic work. Limit failures occur before semantic writes.
- A seeded stdlib fuzzer runs 240 hostile mixed-format, Unicode, malformed
  state, dry-run, and apply cases in CI. Rejected applies must leave the
  target byte-identical.
- Memory text is preserved exactly in the target, backup, and archive.
  Terminal and bidirectional control codes are removed only from displayed
  reports and diffs.
- `HERMES_HOME` selects the active profile. Platform defaults are
  `~/.hermes` on POSIX and `%LOCALAPPDATA%/hermes` on Windows.
- No network access, no subprocess execution, no eval — the file can be
  fully audited as one stdlib-only file.
