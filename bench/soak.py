#!/usr/bin/env python3
"""dream soak test — 90 simulated days of a lived-in Hermes-style memory.

Answers what unit tests cannot: does a memory file under daily churn and a
nightly `dream --apply --budget 2200` stay healthy for months? The
simulation drives the REAL code through dream's injectable clock (`_now`).
Deterministic (seeded).

Daily workload:
  - 1-2 new notes appended (some junk, some updates that re-state an
    earlier subject with new details — supersession bait)
  - every few days a core fact is re-stated verbatim (dedup bait)
  - a nightly dream with the exact Hermes budget

Asserted every night and at day 90:
  - the file NEVER exceeds the budget after apply (hard guarantee)
  - the newest statement of each evolving subject is the one in the file
  - nothing is lost: every entry ever removed appears in the archive
  - a second dream on the final state changes nothing (idempotence)
  - two full runs with the same seed produce byte-identical files

Run:  python3 bench/soak.py
"""
import hashlib
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dream as D                                    # noqa: E402

DAYS = 90
START = datetime(2026, 1, 1, 22, 0, 0)
BUDGET = 2200

EVOLVING = [
    ("provider endpoint", ["provider endpoint for vision is /v1 on the old pool",
                           "provider endpoint for vision is /anthropic — the /v1 claim was wrong",
                           "provider endpoint for vision is /anthropic, and /v1 works only for the standalone pool"]),
    ("deploy target", ["deploy target is the old vps at falkenstein",
                       "deploy target moved to hetzner helsinki with docker compose"]),
    ("user timezone", ["user timezone is utc plus three riyadh",
                       "user timezone is utc plus three riyadh, prefers meetings after ten am"]),
]
JUNK = ("random meeting note", "scratch thought", "temp reminder",
        "ملاحظة عابرة", "خاطرة مؤقتة", "draft idea")

# a large distinct vocabulary so budget-stress entries barely overlap
BUDGET_VOCAB = ("aardvark beacon cinder dahlia ember fathom girder harbor "
                "ingot jasper kelp lantern marlin nectar obsidian pewter "
                "quartz rune sable tundra umber vellum walrus xenon yarrow "
                "zephyr anchor bramble citrus dune eagle flint gulch heron "
                "iris jetty kestrel lupine mango nimbus opal plume quill "
                "raven silo talon usher violet willow yeoman zinc almond "
                "basalt cobalt drift elm fern granite hazel ivory juniper "
                "krill larch moss oak pine reed sage teak vine wren").split()


def run_once(seed):
    random.seed(seed)
    tmp = Path(tempfile.mkdtemp(prefix="dream-soak-"))
    target = tmp / "MEMORY.md"
    clock = [START]
    D._now = lambda: clock[0]

    entries = ["user name is raif, a developer from riyadh"]
    added_log = [entries[0]]
    evolve_idx = {k: 0 for k, _ in EVOLVING}
    target.write_text(entries[0], encoding="utf-8")

    for day in range(1, DAYS + 1):
        clock[0] = START + timedelta(days=day)
        current = target.read_text("utf-8")
        new = []
        # junk churn
        for _ in range(random.randint(1, 2)):
            t = "%s %d on day %d" % (random.choice(JUNK), random.randint(0, 999), day)
            new.append(t)
        # evolving subjects re-stated with new details every ~2 weeks
        for key, versions in EVOLVING:
            if day % random.randint(12, 18) == 0 and evolve_idx[key] < len(versions) - 1:
                evolve_idx[key] += 1
                new.append(versions[evolve_idx[key]])
        # dedup bait: core fact re-stated verbatim weekly
        if day % 7 == 0:
            new.append("user name is raif, a developer from riyadh")
        added_log.extend(new)
        target.write_text(current + D.SECTION_DELIM + D.SECTION_DELIM.join(new),
                          encoding="utf-8")
        # first versions get seeded on their day-0 equivalents
        if day == 1:
            first = [v[0] for _, v in EVOLVING]
            added_log.extend(first)
            target.write_text(target.read_text("utf-8") + D.SECTION_DELIM +
                              D.SECTION_DELIM.join(first), encoding="utf-8")

        rc = D.dream_file(target, {"apply": True, "quiet": True,
                                   "budget": BUDGET, "format": "sections"})
        assert rc == 0, "dream failed on day %d" % day
        _, after = D.parse(target.read_text("utf-8"), "sections")
        size = D.content_size(after, "sections")
        assert size <= BUDGET, "day %d: %d chars > budget %d" % (day, size, BUDGET)

    final = target.read_text("utf-8")
    # newest statement of each evolving subject won
    for key, versions in EVOLVING:
        assert versions[evolve_idx[key]] in final, \
            "newest '%s' statement missing from final memory" % key
    # conservation: everything ever added is in the file, the archive,
    # or survives as the base of a merge (prefix before the merge separator)
    archive = (tmp / "MEMORY.md.dream-archive.md").read_text("utf-8")
    file_bases = [e.split(" ; ")[0] for e in D.parse(final, "sections")[1]]
    lost = []
    for t in added_log:
        base = t.rstrip(".؛; ")
        if t not in final and t not in archive and \
                not any(fb.startswith(base[:40]) for fb in file_bases):
            lost.append(t)
    assert not lost, "lost without archival: %r" % lost[:3]
    # idempotence on the aged file
    before = final
    D.dream_file(target, {"apply": True, "quiet": True,
                          "budget": BUDGET, "format": "sections"})
    assert target.read_text("utf-8") == before, "aged apply not idempotent"

    digest = hashlib.md5(final.encode("utf-8")).hexdigest()
    n_entries = len(D.parse(final, "sections")[1])
    size = D.content_size(D.parse(final, "sections")[1], "sections")
    shutil.rmtree(tmp, ignore_errors=True)
    return digest, n_entries, size, len(added_log)


def budget_stress():
    """The lived-in soak self-limits to ~550 chars, well under 2200, so it
    never triggers the squeeze path (a prior audit correctly flagged the
    "budget held" claim there as vacuous). This leg deliberately feeds
    non-dedupable novel churn against a SMALL budget so squeeze must fire,
    and asserts (a) the hard budget is honored every night and (b) the
    squeeze machinery actually ran (merge and/or archive-for-budget)."""
    random.seed(23)
    tmp = Path(tempfile.mkdtemp(prefix="dream-budget-"))
    target = tmp / "MEMORY.md"
    clock = [START]
    D._now = lambda: clock[0]
    budget = 600
    target.write_text("seed fact zero about the project baseline", encoding="utf-8")
    kinds_seen = set()
    ever_over_before = False
    for day in range(1, 46):
        clock[0] = START + timedelta(days=day)
        cur = target.read_text("utf-8")
        # several genuinely distinct entries/day — each is a bag of unique
        # words with almost no cross-entry overlap, so they neither dedup nor
        # merge (jaccard well under 0.5). This forces the file over the
        # 600-char budget and makes squeeze do real archive-for-budget work.
        novel = [" ".join(random.sample(BUDGET_VOCAB, 7)) for _ in range(6)]
        target.write_text(cur + D.SECTION_DELIM + D.SECTION_DELIM.join(novel),
                          encoding="utf-8")
        _, pre = D.parse(target.read_text("utf-8"), "sections")
        if D.content_size(pre, "sections") > budget:
            ever_over_before = True
        rc = D.dream_file(target, {"apply": True, "quiet": True,
                                   "budget": budget, "format": "sections"})
        assert rc == 0, "budget-stress dream failed on day %d" % day
        _, post = D.parse(target.read_text("utf-8"), "sections")
        assert D.content_size(post, "sections") <= budget, \
            "day %d: %d > budget %d (hard guarantee violated)" % (
                day, D.content_size(post, "sections"), budget)
    # what squeeze actions did the last few nights actually take?
    dreams = (tmp / "DREAMS.md").read_text("utf-8")
    for k in ("merged", "archived-for-budget", "trimmed-tail-clauses", "hard-truncated"):
        if k in dreams:
            kinds_seen.add(k)
    shutil.rmtree(tmp, ignore_errors=True)
    assert ever_over_before, "budget-stress never exceeded the budget pre-squeeze"
    assert kinds_seen, "squeeze path never fired — budget assertion still vacuous"
    return kinds_seen


def main():
    t0 = time.time()
    print("dream soak — %d simulated days, budget %d (real code, injected clock)"
          % (DAYS, BUDGET))
    print("=" * 64)
    d1, n, size, total = run_once(11)
    d2, _, _, _ = run_once(11)
    print("entries ever written: %d  ->  final file: %d entries, %d/%d chars"
          % (total, n, size, BUDGET))
    print("newest-statement-wins: PASS | conservation (file∪archive): PASS | "
          "aged idempotence: PASS")
    det = d1 == d2
    print("determinism across full reruns: %s" % ("PASS" if det else "FAIL"))
    try:
        kinds = budget_stress()
        budget_ok = True
        print("budget stress (small budget, novel churn): squeeze fired %s, "
              "hard budget held every night" % sorted(kinds))
    except AssertionError as e:
        budget_ok = False
        print("budget stress: FAIL — %s" % e)
    print("wall time: %.1f s" % (time.time() - t0))
    ok = det and budget_ok
    print("verdict: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
