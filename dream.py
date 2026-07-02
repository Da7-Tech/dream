#!/usr/bin/env python3
"""
dream — deterministic sleep-cycle consolidation for ANY agent's memory file.

Your coding agent already has a memory file (Hermes MEMORY.md, Claude Code
memory, a notes file in AGENTS.md...). It only ever grows, duplicates pile
up, old facts contradict new ones, and hard character budgets overflow.

`dream` gives that file a nightly sleep cycle, like a brain — and unlike
every other memory consolidator, it is 100% deterministic: no LLM calls,
no API keys, no embeddings server, no token bill. Every single change is
explained in a dream journal, previewed as a diff, and archived (never
destroyed). One file, zero dependencies, works offline.

Sleep phases:
  light sleep   exact + near-duplicate detection (merge, keep the richest)
  deep sleep    supersession: the same subject stated twice -> newest wins,
                the older statement is archived with a reason
  REM           recurring-theme report + contradiction flags (journal only)
  squeeze       optional --budget N: merge related entries, then archive the
                most redundant ones until the file fits its hard limit

Usage:
  python3 dream.py <memory-file>            preview (dry run, default)
  python3 dream.py <memory-file> --apply    write changes (+backup +journal)
  python3 dream.py --hermes [--apply]       consolidate ~/.hermes/memories/*
Options:
  --budget N     enforce a character budget (Hermes MEMORY.md limit: 2200)
  --format F     auto | sections | bullets | paragraphs   (default: auto)
  --max-age D    also archive entries first seen more than D days ago
  --no-merge     disable budget merging (archive-only squeeze)
  --no-supersede disable supersession (dedup only)
  --quiet        print only the summary line

License: MIT  |  https://github.com/Da7-Tech/dream
"""
import sys, os, re, json, math, difflib, hashlib
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

__version__ = "1.1.1"

def _now():
    """Injectable clock — the soak test drives simulated months through it."""
    return datetime.now()


SECTION_DELIM = "\n§\n"          # Hermes memory_tool entry delimiter
NEAR_DUP_JACCARD = 0.85
NEAR_DUP_CONTAINMENT = 0.92   # short entry fully contained in a richer one
SUPERSEDE_SUBJECT_J = 0.5
SUPERSEDE_BODY_LO = 0.40
SUPERSEDE_BODY_HI = 0.85
CONFLICT_BODY_LO = 0.25
MERGE_CLUSTER_SIM = 0.50
SUBJECT_TOKENS = 6
JOURNAL_MAX_BYTES = 64 * 1024


# ────────────────────────────────────────────────────────────────
# Bilingual (EN + AR) tokenization — same stemmer family as `mind`
# ────────────────────────────────────────────────────────────────
_TOKEN = re.compile(r"[\w؀-ۿ]{3,}", re.UNICODE)

STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "from", "this", "these", "those",
    "have", "has", "are", "was", "were", "not", "but", "you", "all", "can",
    "her", "him", "his", "she", "they", "them", "our", "out", "use", "using",
    "used", "what", "when", "where", "which", "who", "why", "how",
    "من", "على", "في", "الى", "إلى", "التي", "التى", "الذي", "الذى", "هذا",
    "هذه", "عند", "قد", "ماذا", "اي", "أي", "لماذا", "كيف", "ما", "عن", "مع",
    "او", "أو", "ثم", "لكن", "بعد", "قبل", "كل", "بعض", "نحن", "انت", "أنت",
    "هو", "هي", "هم", "كان", "يكون", "ان", "أن", "إن", "لا", "لم", "لن",
    "لقد", "ذالك", "ذلك", "هناك",
})

_AR_SUFFIXES = ("تها", "تهن", "تنا", "تهم", "ية", "ون", "ين", "ان",
                "ات", "ها", "هن", "هم", "نا", "ة", "ي", "ت", "ن")
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك", "س")


def stem(w):
    if w and "؀" <= w[0] <= "ۿ":
        s = w
        for p in _AR_PREFIXES:
            if s.startswith(p) and len(s) - len(p) >= 3:
                s = s[len(p):]
                break
        for suf in _AR_SUFFIXES:
            if s.endswith(suf) and len(s) - len(suf) >= 3:
                s = s[:-len(suf)]
                break
        return s or w
    for suf in ("ing", "ied", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            if suf in ("ies", "ied"):
                return w[:-3] + "y"
            if suf == "es":
                if w.endswith(("ases", "eses", "ises")):
                    return w[:-1]
                if w[-3] in "sxz" and len(w) > 4:
                    return w[:-2]
            return w[:-len(suf)]
    return w


def tokens(text):
    out = []
    for raw in _TOKEN.findall((text or "").lower()):
        if raw in STOPWORDS:
            continue
        t = stem(raw)
        if len(t) >= 3 and t not in STOPWORDS:
            out.append(t)
    return out


def jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def containment(a, b):
    """Overlap relative to the smaller set: catches 'same fact, one worded
    with extra detail' pairs that plain Jaccard misses."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / min(len(sa), len(sb))


def _atomic_write(path, data):
    """Atomic, durable, symlink-refusing write: fsync before rename so
    the new content survives power loss, not just process crashes."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("refusing to write through a symlink: %s" % path)
    tmp = str(path) + ".tmp"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | nofollow | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, str(path))


# ────────────────────────────────────────────────────────────────
# Parsing / serialization — three formats, auto-detected
# ────────────────────────────────────────────────────────────────
def detect_format(text):
    if SECTION_DELIM in text or text.strip() == "§" or "\n§ \n" in text:
        return "sections"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    content = [ln for ln in lines if not ln.lstrip().startswith("#")]
    if content and sum(1 for ln in content if ln.startswith(("- ", "* "))) >= max(1, len(content) * 0.6):
        return "bullets"
    return "paragraphs"


def parse(text, fmt):
    """Return (preamble, entries). Preamble (headers/comments before the
    first entry) is preserved verbatim and never consolidated."""
    if fmt == "sections":
        entries = [e.strip() for e in text.split(SECTION_DELIM) if e.strip()]
        return "", entries
    lines = text.splitlines()
    preamble, i = [], 0
    while i < len(lines) and not lines[i].startswith(("- ", "* ")) and (
            lines[i].lstrip().startswith("#") or not lines[i].strip()):
        preamble.append(lines[i])
        i += 1
    if fmt == "bullets":
        entries, cur = [], []
        for ln in lines[i:]:
            if ln.startswith(("- ", "* ")):
                if cur:
                    entries.append("\n".join(cur).strip())
                cur = [ln]
            elif ln.strip() and cur:
                cur.append(ln)          # continuation line
            elif not ln.strip():
                continue
            else:
                preamble.append(ln) if not cur else cur.append(ln)
        if cur:
            entries.append("\n".join(cur).strip())
        return "\n".join(preamble).strip(), entries
    # paragraphs
    blocks, cur = [], []
    for ln in lines[i:]:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append("\n".join(cur).strip())
            cur = []
    if cur:
        blocks.append("\n".join(cur).strip())
    return "\n".join(preamble).strip(), blocks


def serialize(preamble, entries, fmt):
    if fmt == "sections":
        return SECTION_DELIM.join(entries)
    body = ("\n".join(entries) if fmt == "bullets"
            else "\n\n".join(entries))
    if preamble:
        return preamble + "\n\n" + body + "\n"
    return body + "\n"


def content_size(entries, fmt):
    """Character count exactly as the host agent measures it.
    Hermes counts len('\\n§\\n'.join(entries)) — we match that."""
    if fmt == "sections":
        return len(SECTION_DELIM.join(entries))
    return sum(len(e) for e in entries) + max(0, len(entries) - 1)


# ────────────────────────────────────────────────────────────────
# The consolidation pipeline
# ────────────────────────────────────────────────────────────────
class Entry:
    __slots__ = ("text", "toks", "pos")

    def __init__(self, text, pos):
        self.text = text
        self.toks = tokens(text)
        self.pos = pos          # original position: later = newer (append-log)

    @property
    def eid(self):
        norm = " ".join(self.toks) or self.text.strip().lower()
        return hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]

    def subject(self):
        return self.toks[:SUBJECT_TOKENS]


class Action:
    def __init__(self, phase, kind, reason, removed=None, result=None):
        self.phase, self.kind, self.reason = phase, kind, reason
        self.removed, self.result = removed, result

    def describe(self):
        out = "[%s] %s — %s" % (self.phase, self.kind, self.reason)
        if self.removed:
            out += "\n    archived: %s" % _short(self.removed)
        if self.result:
            out += "\n    kept/now: %s" % _short(self.result)
        return out


def _short(t, n=100):
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n] + "…"


class Dreamer:
    def __init__(self, entries, fmt, opts):
        self.entries = [Entry(t, i) for i, t in enumerate(entries)]
        self.fmt = fmt
        self.opts = opts
        self.actions = []
        self.flags = []       # journal-only observations
        self.themes = []

    # -- light sleep: duplicates ------------------------------------
    def light_sleep(self):
        seen = {}
        kept = []
        for e in self.entries:
            if e.eid in seen:
                self.actions.append(Action(
                    "light", "exact-duplicate",
                    "identical after normalization; one copy is enough",
                    removed=e.text, result=seen[e.eid].text))
                continue
            seen[e.eid] = e
            kept.append(e)
        self.entries = kept
        kept = []
        for e in self.entries:
            dup_of = None
            for k in kept:
                if (jaccard(e.toks, k.toks) >= NEAR_DUP_JACCARD or
                        containment(e.toks, k.toks) >= NEAR_DUP_CONTAINMENT):
                    dup_of = k
                    break
            if dup_of is None:
                kept.append(e)
            else:
                # keep whichever says more; position of the survivor stays
                rich, poor = (e, dup_of) if len(e.text) > len(dup_of.text) else (dup_of, e)
                if rich is e:
                    kept[kept.index(dup_of)] = e
                self.actions.append(Action(
                    "light", "near-duplicate",
                    "same fact worded twice (%.0f%% token overlap); kept the richer wording"
                    % (jaccard(e.toks, dup_of.toks) * 100),
                    removed=poor.text, result=rich.text))
        self.entries = kept

    # -- deep sleep: supersession -----------------------------------
    def deep_sleep(self):
        if self.opts.get("no_supersede"):
            return
        removed = set()
        es = self.entries
        for i in range(len(es)):
            for j in range(i + 1, len(es)):
                a, b = es[i], es[j]
                if a.eid in removed or b.eid in removed:
                    continue
                sj = jaccard(a.subject(), b.subject())
                bj = jaccard(a.toks, b.toks)
                if sj >= SUPERSEDE_SUBJECT_J and SUPERSEDE_BODY_LO <= bj < SUPERSEDE_BODY_HI:
                    old, new = (a, b) if a.pos < b.pos else (b, a)
                    removed.add(old.eid)
                    self.actions.append(Action(
                        "deep", "superseded",
                        "same subject stated again later in the file "
                        "(subject overlap %.0f%%, body %.0f%%); the newer "
                        "statement wins, the older one is archived"
                        % (sj * 100, bj * 100),
                        removed=old.text, result=new.text))
                elif sj >= SUPERSEDE_SUBJECT_J and CONFLICT_BODY_LO <= bj < SUPERSEDE_BODY_LO:
                    self.flags.append(
                        "possible conflict (not auto-resolved, overlap too low "
                        "to be safe):\n    a: %s\n    b: %s"
                        % (_short(a.text), _short(b.text)))
        self.entries = [e for e in self.entries if e.eid not in removed]

    # -- optional age-based archiving --------------------------------
    def age_out(self, state, max_age_days):
        if not max_age_days:
            return
        now = _now()
        kept = []
        for e in self.entries:
            first_seen = state.get(e.eid, {}).get("first_seen")
            if first_seen:
                try:
                    age = (now - datetime.fromisoformat(first_seen)).days
                except ValueError:
                    age = 0
                if age > max_age_days:
                    self.actions.append(Action(
                        "deep", "aged-out",
                        "first seen %d days ago (limit %d, --max-age); "
                        "archived, restore from the archive if still needed"
                        % (age, max_age_days),
                        removed=e.text))
                    continue
            kept.append(e)
        self.entries = kept

    # -- REM: themes + insight (journal only) -------------------------
    def rem(self):
        df = Counter()
        for e in self.entries:
            for t in set(e.toks):
                df[t] += 1
        recurring = [(t, c) for t, c in df.most_common(30) if c >= 3]
        self.themes = recurring[:8]

    # -- squeeze: enforce a hard character budget ---------------------
    def squeeze(self, budget):
        if not budget:
            return
        size = content_size([e.text for e in self.entries], self.fmt)
        if size <= budget:
            return
        # step 1: merge related clusters (keep every novel clause)
        if not self.opts.get("no_merge"):
            merged = True
            while merged and content_size([e.text for e in self.entries], self.fmt) > budget:
                merged = False
                best = None
                es = self.entries
                for i in range(len(es)):
                    for j in range(i + 1, len(es)):
                        sim = jaccard(es[i].toks, es[j].toks)
                        # same leading subject (identical first 3 stemmed
                        # tokens) is merge-eligible even at lower overlap
                        same_lead = (len(es[i].toks) >= 3 and
                                     es[i].toks[:3] == es[j].toks[:3])
                        if (sim >= MERGE_CLUSTER_SIM or same_lead) and (
                                best is None or sim > best[0]):
                            best = (sim, i, j)
                if best:
                    _, i, j = best
                    a, b = self.entries[i], self.entries[j]
                    base, other = (a, b) if len(a.text) >= len(b.text) else (b, a)
                    merged_text = _merge_texts(base.text, other.text)
                    if len(merged_text) > budget:
                        # a merge that cannot fit the budget is pointless —
                        # fall through to redundancy-based archiving instead
                        break
                    e = Entry(merged_text, min(a.pos, b.pos))
                    self.actions.append(Action(
                        "squeeze", "merged",
                        "over budget; two related entries (%.0f%% overlap) "
                        "merged into one, novel clauses preserved" % (best[0] * 100),
                        removed=other.text, result=merged_text))
                    self.entries = [x for k, x in enumerate(self.entries)
                                    if k not in (i, j)] + [e]
                    merged = True
        # step 2: archive the most redundant entries until we fit
        while content_size([e.text for e in self.entries], self.fmt) > budget \
                and len(self.entries) > 1:
            victim = min(self.entries, key=self._info_density)
            self.entries.remove(victim)
            self.actions.append(Action(
                "squeeze", "archived-for-budget",
                "still over budget; this entry has the lowest unique "
                "information per character (most of its content appears "
                "in other entries)",
                removed=victim.text))
        # step 3: a single remaining entry that still exceeds the budget is
        # trimmed clause by clause from the tail (trimmed clauses archived)
        if len(self.entries) == 1 and len(self.entries[0].text) > budget:
            e = self.entries[0]
            clauses = re.split(r"(?<=[.;؛!؟?])\s+|\n+| ; ", e.text)
            kept, dropped = [], []
            used = 0
            for c in clauses:
                if not c.strip():
                    continue
                if used + len(c) + 2 <= budget:
                    kept.append(c)
                    used += len(c) + 2
                else:
                    dropped.append(c)
            if kept and dropped:
                self.entries[0] = Entry(" ".join(kept).strip(), e.pos)
                self.actions.append(Action(
                    "squeeze", "trimmed-tail-clauses",
                    "single entry larger than the whole budget; trailing "
                    "clauses archived to fit",
                    removed=" ; ".join(dropped),
                    result=self.entries[0].text))
            elif dropped:
                # no clause boundary fits (one long unpunctuated entry, or an
                # absurdly small budget): hard-cut at the last word boundary.
                # The budget is a guarantee, not a suggestion — the cut tail
                # is archived like everything else.
                cut = e.text[:max(0, budget)]
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                tail = e.text[len(cut):].strip()
                if cut.strip():
                    self.entries[0] = Entry(cut.strip(), e.pos)
                    self.actions.append(Action(
                        "squeeze", "hard-truncated",
                        "entry has no clause boundaries that fit the budget; "
                        "cut at a word boundary to honor the hard limit",
                        removed=tail, result=cut.strip()))

    def _info_density(self, entry):
        others = Counter()
        for e in self.entries:
            if e is not entry:
                others.update(set(e.toks))
        unique = sum(1 for t in set(entry.toks) if others[t] == 0)
        return (unique + 0.1) / max(1, len(entry.text))


def _merge_texts(base, other):
    """Deterministic merge: append clauses of `other` whose tokens add
    information not already present in `base`."""
    base_toks = set(tokens(base))
    clauses = re.split(r"(?<=[.;؛!؟?])\s+|\n+", other)
    novel = []
    for c in clauses:
        c = c.strip(" -•\t")
        if not c:
            continue
        ct = set(tokens(c))
        if ct and not ct <= base_toks:
            novel.append(c.rstrip(".؛;"))
            base_toks |= ct
    if not novel:
        return base
    sep = " ; "
    return base.rstrip(".؛; ") + sep + sep.join(novel)


# ────────────────────────────────────────────────────────────────
# State, archive, journal
# ────────────────────────────────────────────────────────────────
def state_path(target):
    return target.parent / (".%s.dream-state.json" % target.name)


def load_state(target):
    p = state_path(target)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def save_state(target, entries, old_state):
    now = _now().isoformat(timespec="seconds")
    st = {}
    for e in entries:
        prev = old_state.get(e.eid, {})
        st[e.eid] = {
            "first_seen": prev.get("first_seen", now),
            "last_seen": now,
            "runs_seen": prev.get("runs_seen", 0) + 1,
            "preview": _short(e.text, 60),
        }
    _atomic_write(state_path(target), json.dumps(st, ensure_ascii=False, indent=1))


def append_archive(target, actions):
    arch = target.parent / ("%s.dream-archive.md" % target.name)
    lines = []
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    for a in actions:
        if a.removed:
            lines.append("## %s — %s (%s)\n\n%s\n" % (stamp, a.kind, a.reason, a.removed))
    if not lines:
        return None
    prev = arch.read_text("utf-8") if arch.exists() else "# dream archive — nothing is ever deleted, only moved here\n\n"
    _atomic_write(arch, prev + "\n".join(lines) + "\n")
    return arch


def append_journal(target, report):
    j = target.parent / "DREAMS.md"
    prev = j.read_text("utf-8") if j.exists() else "# DREAMS.md — dream journal\n\n"
    text = prev + report + "\n"
    if len(text.encode("utf-8")) > JOURNAL_MAX_BYTES:      # rotate: keep tail
        text = "# DREAMS.md — dream journal (rotated)\n\n" + report + "\n"
    _atomic_write(j, text)
    return j


# ────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────
def dream_file(target, opts):
    target = Path(target).expanduser().resolve()
    if not target.exists():
        print("error: %s does not exist" % target, file=sys.stderr)
        return 1
    original = target.read_text("utf-8")
    fmt = opts.get("format") or detect_format(original)
    preamble, entries = parse(original, fmt)
    if not entries:
        print("%s: no entries found (format: %s) — nothing to do." % (target.name, fmt))
        return 0

    d = Dreamer(entries, fmt, opts)
    d.light_sleep()
    d.deep_sleep()
    state = load_state(target)
    d.age_out(state, opts.get("max_age"))
    d.rem()
    d.squeeze(opts.get("budget"))

    new_entries = [e.text for e in sorted(d.entries, key=lambda e: e.pos)]
    new_text = serialize(preamble, new_entries, fmt)
    size_before = content_size(entries, fmt)
    size_after = content_size(new_entries, fmt)

    # build the journal report
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    rep = ["## dream — %s — %s" % (stamp, target.name), ""]
    rep.append("- entries: %d -> %d | chars: %d -> %d%s"
               % (len(entries), len(new_entries), size_before, size_after,
                  (" | budget: %d" % opts["budget"]) if opts.get("budget") else ""))
    if d.actions:
        rep.append("")
        for a in d.actions:
            rep.append("- " + a.describe().replace("\n", "\n  "))
    else:
        rep.append("- memory is already clean: no duplicates, no supersessions.")
    if d.flags:
        rep.append("\n### flags (no action taken)")
        for f in d.flags:
            rep.append("- " + f.replace("\n", "\n  "))
    if d.themes:
        rep.append("\n### recurring themes")
        rep.append("- " + ", ".join("%s (x%d)" % (t, c) for t, c in d.themes))
    report = "\n".join(rep)

    changed = new_text.strip() != original.strip()
    quiet = opts.get("quiet")

    if not opts.get("apply"):
        if not quiet:
            print(report)
            if changed:
                print("\n--- diff preview ---")
                for line in difflib.unified_diff(
                        original.splitlines(), new_text.splitlines(),
                        fromfile=str(target), tofile=str(target) + " (after dream)",
                        lineterm=""):
                    print(line)
                print("\ndry run — nothing written. add --apply to consolidate.")
            else:
                print("\nno changes needed.")
        else:
            print("%s: %d -> %d entries, %d -> %d chars (dry run)"
                  % (target.name, len(entries), len(new_entries),
                     size_before, size_after))
        return 0

    # apply: backup -> write -> archive -> journal -> state
    if changed:
        backup = target.parent / ("%s.bak-dream-%s" % (
            target.name, _now().strftime("%Y%m%d-%H%M%S")))
        _atomic_write(backup, original)
        _atomic_write(target, new_text)
        arch = append_archive(target, d.actions)
        j = append_journal(target, report)
        save_state(target, d.entries, state)
        print("%s consolidated: %d -> %d entries, %d -> %d chars"
              % (target.name, len(entries), len(new_entries),
                 size_before, size_after))
        if not quiet:
            print("  backup:  %s" % backup.name)
            if arch:
                print("  archive: %s (every removed entry, with reasons)" % arch.name)
            print("  journal: %s" % j.name)
    else:
        save_state(target, d.entries, state)
        append_journal(target, report)
        print("%s: already clean, nothing to change." % target.name)
    return 0


def hermes_targets():
    """Locate Hermes memory files + their configured char limits."""
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    cfg = home / "config.yaml"
    mem_limit, user_limit = 2200, 1375       # Hermes defaults
    if cfg.exists():
        try:
            text = cfg.read_text("utf-8")
            m = re.search(r"memory_char_limit:\s*(\d+)", text)
            if m:
                mem_limit = int(m.group(1))
            m = re.search(r"user_char_limit:\s*(\d+)", text)
            if m:
                user_limit = int(m.group(1))
        except OSError:
            pass
    out = []
    for name, limit in (("MEMORY.md", mem_limit), ("USER.md", user_limit)):
        p = home / "memories" / name
        if p.exists():
            out.append((p, limit))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if argv[0] in ("-v", "--version", "version"):
        print(__version__)
        return 0
    opts = {"apply": False, "budget": None, "format": None,
            "max_age": None, "no_merge": False, "no_supersede": False,
            "quiet": False}
    files, hermes = [], False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--apply":
            opts["apply"] = True
        elif a == "--hermes":
            hermes = True
        elif a == "--quiet":
            opts["quiet"] = True
        elif a == "--no-merge":
            opts["no_merge"] = True
        elif a == "--no-supersede":
            opts["no_supersede"] = True
        elif a in ("--budget", "--max-age"):
            key = "budget" if a == "--budget" else "max_age"
            i += 1
            if i >= len(argv):
                print("error: %s needs a number (e.g. %s 2200)" % (a, a),
                      file=sys.stderr)
                return 2
            try:
                val = int(argv[i])
            except ValueError:
                print("error: %s expects an integer, got %r" % (a, argv[i]),
                      file=sys.stderr)
                return 2
            if val <= 0:
                print("error: %s must be positive" % a, file=sys.stderr)
                return 2
            opts[key] = val
        elif a == "--format":
            i += 1
            if argv[i] not in ("auto", "sections", "bullets", "paragraphs"):
                print("error: unknown format %r" % argv[i], file=sys.stderr)
                return 2
            opts["format"] = None if argv[i] == "auto" else argv[i]
        elif a.startswith("-"):
            print("error: unknown option %s (see --help)" % a, file=sys.stderr)
            return 2
        else:
            files.append(a)
        i += 1

    rc = 0
    if hermes:
        targets = hermes_targets()
        if not targets:
            home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
            print("error: no Hermes memory found (%s)" % (home / "memories"),
                  file=sys.stderr)
            return 1
        for path, limit in targets:
            o = dict(opts)
            if o["budget"] is None:
                o["budget"] = limit
            o["format"] = o["format"] or "sections"
            rc = max(rc, dream_file(path, o))
    for f in files:
        rc = max(rc, dream_file(f, opts))
    if not hermes and not files:
        print("error: no target. pass a memory file or --hermes (see --help)",
              file=sys.stderr)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
