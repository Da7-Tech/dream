#!/usr/bin/env python3
"""Seeded hostile-state fuzz for dream's real file runner.

The fuzzer is deterministic and stdlib-only. It exercises mixed formats,
Unicode, terminal controls, malformed state, bounded budgets, and both dry-run
and apply paths. Its contract is intentionally simple: no uncaught exception,
no invalid return code, and no target mutation after a rejected apply.

Run:  python3 bench/fuzz.py
"""
import io
import json
import random
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dream as D                                    # noqa: E402

CASES = 240
SEED = 1407
WORDS = (
    "alpha bravo charlie delta echo foxtrot quartz velvet cobalt amber "
    "routing storage cache backup deploy provider database timezone user "
    "project memory corrected newest archive stable deterministic"
).split()
UNICODE = (
    "المستخدم يفضل الوضع الداكن",
    "قاعدة بيانات المشروع محدثة",
    "数据库使用了缓存层",
    "服务器在法兰克福",
    "ความจำของโครงการ",
    "проект использует кэш",
)


def random_entry(rng, index):
    parts = rng.sample(WORDS, rng.randint(4, 9))
    if rng.random() < 0.25:
        parts.append(rng.choice(UNICODE))
    if rng.random() < 0.08:
        parts.append("\x1b[31m")
    if rng.random() < 0.08:
        parts.append("\u202e")
    parts.append("case-%d" % index)
    return " ".join(parts)


def render(entries, fmt):
    if fmt == "sections":
        return D.SECTION_DELIM.join(entries)
    if fmt == "bullets":
        return "# Memory\n\n" + "\n".join("- " + item for item in entries) + "\n"
    return "\n\n".join(entries) + "\n"


def run_case(rng, index):
    root = Path(tempfile.mkdtemp(prefix="dream-fuzz-"))
    try:
        target = root / "MEMORY.md"
        fmt = rng.choice(("sections", "bullets", "paragraphs"))
        entries = [random_entry(rng, i) for i in range(rng.randint(1, 20))]
        if rng.random() < 0.55:
            entries.append(rng.choice(entries))
        original = render(entries, fmt)
        target.write_text(original, encoding="utf-8")

        if rng.random() < 0.25:
            state = D.state_path(target)
            if rng.random() < 0.5:
                state.write_text("{not-json", encoding="utf-8")
            else:
                _, parsed = D.parse(original, fmt)
                eid = D.Entry(parsed[0], 0).eid
                state.write_text(
                    json.dumps({
                        eid: rng.choice((
                            [], "text", 7,
                            {"runs_seen": "many", "first_seen": 17},
                        ))
                    }),
                    encoding="utf-8")

        opts = {
            "apply": rng.random() < 0.65,
            "quiet": True,
            "format": fmt if rng.random() < 0.5 else None,
            "no_merge": rng.random() < 0.2,
            "no_supersede": rng.random() < 0.2,
            "budget": rng.choice((None, 80, 160, 400, 800)),
            "max_age": rng.choice((None, 1, 30)),
        }
        before = target.read_bytes()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = D.dream_file(target, opts)
        if rc not in (0, 1):
            raise AssertionError("case %d returned %r" % (index, rc))
        if rc != 0 and target.read_bytes() != before:
            raise AssertionError(
                "case %d changed the target after rejection" % index)
        if rc == 0:
            target.read_text("utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rerun = D.dream_file(target, {
                    "apply": False, "quiet": True, "format": fmt})
            if rerun != 0:
                raise AssertionError(
                    "case %d produced an unreadable follow-up state" % index)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    rng = random.Random(SEED)
    D._now = lambda: datetime(2026, 7, 15, 4, 0, 0)
    for index in range(CASES):
        run_case(rng, index)
    print("dream fuzz: PASS (%d deterministic hostile cases, seed %d)"
          % (CASES, SEED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
