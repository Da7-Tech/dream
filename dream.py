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
  python3 dream.py --hermes [--apply]       consolidate the active Hermes profile
Options:
  --budget N     enforce a character budget (Hermes MEMORY.md limit: 2200)
  --format F     auto | sections | bullets | paragraphs   (default: auto)
  --max-age D    also archive entries first seen more than D days ago
  --no-merge     disable budget merging (archive-only squeeze)
  --no-supersede disable supersession (dedup only)
  --quiet        print only the summary line

License: MIT  |  https://github.com/Da7-Tech/dream
"""
import sys, os, re, json, math, difflib, hashlib, time, tempfile, stat, threading
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
from contextlib import contextmanager

__version__ = "1.4.0"

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
MAX_TARGET_BYTES = 10_000_000
MAX_SIDE_BYTES = 10_000_000
MAX_PENDING_BYTES = 50_000_000
MAX_ENTRIES = 10_000
MAX_DREAM_COMPARISONS = 200_000
LOCK_TIMEOUT_SECONDS = 30.0


class UnsafePathError(ValueError):
    """A dream artifact is not a private regular file."""


class FileLimitError(ValueError):
    """An input or operation exceeds a documented resource bound."""


class StaleTargetError(ValueError):
    """A file changed after it was read for a preserving rewrite."""


_NO_EXPECTATION = object()
_EXPECTED_MISSING = object()


# ────────────────────────────────────────────────────────────────
# Bilingual (EN + AR) + script-aware tokenization — kept in LOCK-STEP with
# `mind`'s tokenizer/stemmer family (mind.py), so a memory file consolidated
# by either tool tokenizes identically. Space-separated scripts (Latin,
# Cyrillic, Arabic, Greek, ...) keep whole words >= 3 chars; CJK / kana /
# Hangul / Thai runs — which don't separate meaning units with spaces — are
# indexed as character BIGRAMS instead (the standard search-engine
# technique). Without bigrams a Chinese/Japanese entry collapsed to one
# opaque token that only matched an IDENTICAL whole run, so dedup and
# supersession never fired on CJK memories (ported from mind 5.6.0).
# ────────────────────────────────────────────────────────────────
_WORD_RUN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)
# explicit \u escapes (not literal glyphs): the compatibility-ideograph
# start U+F900 NFC-normalizes to U+8C48, so a literal here silently widens
# the range over the Private Use Area — escapes are corruption-proof.
_NOSPACE_RE = re.compile(
    "[\u2E80-\u9FFF"    # CJK radicals + unified ideographs
    "\u3400-\u4DBF"     # CJK extension A
    "\uF900-\uFAFF"     # CJK compatibility ideographs
    "\u3040-\u30FF"     # hiragana + katakana
    "\uAC00-\uD7AF"     # hangul syllables
    "\u0E00-\u0E7F]")   # thai

STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "from", "this", "these", "those",
    "have", "has", "are", "was", "were", "not", "but", "you", "all", "can",
    "her", "him", "his", "she", "they", "them", "our", "out", "use", "using",
    "used", "what", "when", "where", "which", "who", "why", "how",
    # function words shared with mind's stopword set (6.1.0)
    "is", "be", "been", "being", "does", "did", "will", "its", "it",
    "my", "your", "their",
    "من", "على", "في", "الى", "إلى", "التي", "التى", "الذي", "الذى", "هذا",
    "هذه", "عند", "قد", "ماذا", "اي", "أي", "لماذا", "كيف", "ما", "عن", "مع",
    "او", "أو", "ثم", "لكن", "بعد", "قبل", "كل", "بعض", "نحن", "انت", "أنت",
    "هو", "هي", "هم", "كان", "يكون", "ان", "أن", "إن", "لا", "لم", "لن",
    "لقد", "ذالك", "ذلك", "هناك",
})

_AR_SUFFIXES = ("تها", "تهن", "تنا", "تهم", "ية", "ون", "ين", "ان",
                "ات", "ها", "هن", "هم", "نا", "ة", "ي", "ت", "ن")
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك", "س")
# Arabic broken plurals can't be reached by suffix stripping; a small seed
# dictionary unifies singular + broken plural onto ONE canonical stem, so the
# two spellings of the same subject count as the same fact for dedup and
# supersession (ported from mind: without it "قاعدة" and "قواعد" tokenized
# differently and a restated fact was never recognized as a duplicate).
_BROKEN_PLURALS = {
    "قاعدة": "قاعد", "مدينة": "مدين", "دولة": "دول", "أداة": "أدا",
    "مشروع": "مشروع", "ملف": "ملف", "وكيل": "وكيل", "خبير": "خبير",
    "قرار": "قرار", "رابط": "رابط", "بيان": "بيان", "حرف": "حرف",
    "كلمة": "كلم", "عقدة": "عقد", "نموذج": "نموذج",
    "قواعد": "قاعد", "مدن": "مدين", "دول": "دول", "أدوات": "أدا",
    "مشاريع": "مشروع", "ملفات": "ملف", "وكلاء": "وكيل", "خبراء": "خبير",
    "قرارات": "قرار", "روابط": "رابط", "بيانات": "بيان", "حروف": "حرف",
    "كلمات": "كلم", "عقد": "عقد", "نماذج": "نموذج",
    "وظيفة": "وظيف", "وظائف": "وظيف", "رسالة": "رسال", "رسائل": "رسال",
    "جدول": "جدول", "جداول": "جدول",
}


def _bigrams(chars):
    if len(chars) < 2:
        return ["".join(chars)]
    return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]


def _tokenize(text):
    """Script-aware tokenizer: whole words for spaced scripts, character
    bigrams for CJK/kana/Hangul/Thai runs. Shared verbatim with mind."""
    out = []
    for run in _WORD_RUN.findall(text or ""):
        alpha, nospace = [], []
        for ch in run:
            if _NOSPACE_RE.match(ch):
                if alpha:
                    if len(alpha) >= 3:
                        out.append("".join(alpha))
                    alpha = []
                nospace.append(ch)
            else:
                if nospace:
                    out.extend(_bigrams(nospace))
                    nospace = []
                alpha.append(ch)
        if len(alpha) >= 3:
            out.append("".join(alpha))
        if nospace:
            out.extend(_bigrams(nospace))
    return out


def stem(w):
    if w and "؀" <= w[0] <= "ۿ":
        # full-word broken-plural lookup FIRST: stripping a "prefix" that is
        # actually the first ROOT letter (كلمة -> لمة) otherwise bypasses the
        # dictionary entirely (ported from mind's auditor finding)
        if w in _BROKEN_PLURALS:
            return _BROKEN_PLURALS[w]
        s = w
        for p in _AR_PREFIXES:
            if s.startswith(p) and len(s) - len(p) >= 3:
                stripped = s[len(p):]
                if stripped in _BROKEN_PLURALS:
                    return _BROKEN_PLURALS[stripped]
                s = stripped
                break
        if s in _BROKEN_PLURALS:
            return _BROKEN_PLURALS[s]
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
    for raw in _tokenize((text or "").lower()):
        if raw in STOPWORDS:
            continue
        t = stem(raw)
        # no-space-script bigrams are 1-2 chars by construction — only
        # alphabetic tokens carry the 3-char floor (mirrors mind)
        if (len(t) < 3 and not _NOSPACE_RE.match(t)) or t in STOPWORDS:
            continue
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


def _same_sequence(a, b):
    """True if the two token lists carry the shared tokens in the SAME
    relative order — the guard that distinguishes 'same fact reworded' from
    'opposite fact, words reversed'. The longer list may add tokens (a
    richer restatement) but must not REORDER the shared ones."""
    common = set(a) & set(b)
    sa = [t for t in a if t in common]
    sb = [t for t in b if t in common]
    return sa == sb


def _absolute_path(path):
    """Expand a path without resolving away a final symlink."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def _reject_symlinked_parents(path, boundary):
    """Reject a symlinked parent or a path outside the trust boundary."""
    boundary = os.path.abspath(str(boundary))
    current = os.path.abspath(str(Path(path).parent))
    while True:
        if os.path.islink(current):
            raise UnsafePathError(
                "refusing to use a symlinked parent: %s" % current)
        if current == boundary:
            return
        parent = os.path.dirname(current)
        if parent == current:
            raise UnsafePathError(
                "path %s escapes boundary %s" % (path, boundary))
        current = parent


def _open_regular(path, flags, mode=0o600, boundary=None):
    """Open one private regular file without following or blocking on it."""
    path = _absolute_path(path)
    boundary = _absolute_path(boundary or path.parent)
    before = None
    if os.name == "nt":
        _reject_symlinked_parents(path, boundary)
        if path.is_symlink():
            raise UnsafePathError("refusing symlink file %s" % path)
        try:
            before = os.lstat(str(path))
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise UnsafePathError("refusing unsafe file %s" % path)
        except FileNotFoundError:
            before = None
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    parent_fd = None
    try:
        if os.name != "nt" and os.open in getattr(os, "supports_dir_fd", set()):
            try:
                relative = path.relative_to(boundary)
            except ValueError:
                raise UnsafePathError(
                    "file %s escapes boundary %s" % (path, boundary))
            dir_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                         getattr(os, "O_NOFOLLOW", 0))
            parent_fd = os.open(str(boundary), dir_flags)
            for part in relative.parent.parts:
                next_fd = os.open(part, dir_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            for attempt in range(20):
                try:
                    fd = os.open(path.name, flags, mode, dir_fd=parent_fd)
                    break
                except FileNotFoundError:
                    if not (flags & os.O_CREAT) or attempt == 19:
                        raise
                    time.sleep(0.005)
        else:
            _reject_symlinked_parents(path, boundary)
            if path.is_symlink():
                raise UnsafePathError("refusing symlink file %s" % path)
            fd = os.open(str(path), flags, mode)
    except OSError as exc:
        if isinstance(exc, (FileNotFoundError, PermissionError)):
            raise
        raise UnsafePathError(
            "refusing unsafe file %s: %s" % (path, exc))
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    try:
        info = os.fstat(fd)
        read_only = (flags & getattr(os, "O_ACCMODE", 3)) == os.O_RDONLY
        valid_links = info.st_nlink in ((0, 1) if read_only else (1,))
        if not stat.S_ISREG(info.st_mode) or not valid_links:
            raise UnsafePathError(
                "refusing %s: regular, single-link file required" % path)
        if os.name == "nt":
            try:
                after = os.lstat(str(path))
            except FileNotFoundError:
                raise StaleTargetError(
                    "file changed during open: %s" % path)
            if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                raise StaleTargetError(
                    "file changed during open: %s" % path)
            if before is not None and (
                    before.st_dev, before.st_ino) != (
                    after.st_dev, after.st_ino):
                raise StaleTargetError(
                    "file changed during open: %s" % path)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_text_retry(path, max_bytes=MAX_SIDE_BYTES, with_identity=False,
                     boundary=None):
    """Read a bounded regular UTF-8 file, retrying Windows replace races."""
    for attempt in range(200):
        try:
            fd = _open_regular(
                path, os.O_RDONLY, boundary=boundary or Path(path).parent)
            try:
                before = os.fstat(fd)
                if before.st_size > max_bytes:
                    raise FileLimitError(
                        "%s exceeds the %d-byte limit" % (path, max_bytes))
                chunks = []
                remaining = max_bytes + 1
                while remaining:
                    chunk = os.read(fd, min(1_048_576, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if len(payload) > max_bytes:
                    raise FileLimitError(
                        "%s exceeds the %d-byte limit" % (path, max_bytes))
                after = os.fstat(fd)
                if _identity(before) != _identity(after):
                    if attempt == 199:
                        raise StaleTargetError(
                            "%s changed while it was being read" % path)
                    continue
                text = payload.decode("utf-8")
                return (text, _identity(after)) if with_identity else text
            finally:
                os.close(fd)
        except PermissionError:
            if os.name != "nt" or attempt == 199:
                raise
            time.sleep(0.05)
        except StaleTargetError:
            if attempt == 199:
                raise
            time.sleep(0.005)


def _read_optional(path, max_bytes=MAX_SIDE_BYTES, with_identity=False,
                   boundary=None):
    try:
        return _read_text_retry(
            path, max_bytes=max_bytes, with_identity=with_identity,
            boundary=boundary)
    except FileNotFoundError:
        return ("", _EXPECTED_MISSING) if with_identity else ""


def _write_all(fd, data):
    """Write every byte or raise; os.write may legally return short."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write: os.write made no progress")
        view = view[written:]


def _atomic_write(path, data, boundary=None,
                  expected_identity=_NO_EXPECTATION, mode=0o600):
    """Atomic, durable, private-file write with stale-target detection."""
    path = _absolute_path(path)
    boundary = _absolute_path(boundary or path.parent)
    payload = data.encode("utf-8") if isinstance(data, str) else data

    if os.name != "nt" and os.rename in getattr(
            os, "supports_dir_fd", set()):
        try:
            relative = path.relative_to(boundary)
        except ValueError:
            raise UnsafePathError(
                "path %s escapes boundary %s" % (path, boundary))
        dir_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
        try:
            parent_fd = os.open(str(boundary), dir_flags)
        except OSError as exc:
            raise UnsafePathError(
                "refusing unsafe boundary %s: %s" % (boundary, exc))
        try:
            for part in relative.parent.parts:
                next_fd = os.open(part, dir_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            target_mode = mode
            try:
                current = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            current_identity = (
                _identity(current) if current is not None
                else _EXPECTED_MISSING)
            if expected_identity is not _NO_EXPECTATION and \
                    current_identity != expected_identity:
                raise StaleTargetError(
                    "%s changed after it was read" % path)
            if current is not None:
                if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                    raise UnsafePathError(
                        "refusing unsafe atomic-write target: %s" % path)
                target_mode = stat.S_IMODE(current.st_mode)
            tmp_name = ".%s.%d.%d.%d.tmp" % (
                path.name, os.getpid(), threading.get_ident(), time.time_ns())
            fd = os.open(
                tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                getattr(os, "O_NOFOLLOW", 0), target_mode, dir_fd=parent_fd)
            replaced = False
            try:
                try:
                    try:
                        os.fchmod(fd, target_mode)
                    except (AttributeError, OSError):
                        pass
                    _write_all(fd, payload)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                    fd = None
                if expected_identity is not _NO_EXPECTATION:
                    try:
                        latest = os.stat(
                            path.name, dir_fd=parent_fd,
                            follow_symlinks=False)
                        latest_identity = _identity(latest)
                    except FileNotFoundError:
                        latest_identity = _EXPECTED_MISSING
                    if latest_identity != expected_identity:
                        raise StaleTargetError(
                            "%s changed during rewrite" % path)
                os.replace(
                    tmp_name, path.name, src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd)
                replaced = True
                os.fsync(parent_fd)
            finally:
                if fd is not None:
                    os.close(fd)
                if not replaced:
                    try:
                        os.unlink(tmp_name, dir_fd=parent_fd)
                    except OSError:
                        pass
        finally:
            os.close(parent_fd)
        return

    if path.is_symlink():
        raise UnsafePathError(
            "refusing to write through a symlink: %s" % path)
    _reject_symlinked_parents(path, boundary)
    target_mode = mode
    if path.exists():
        current = os.lstat(str(path))
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise UnsafePathError(
                "refusing unsafe atomic-write target: %s" % path)
        target_mode = stat.S_IMODE(current.st_mode)
        current_identity = _identity(current)
    else:
        current_identity = _EXPECTED_MISSING
    if expected_identity is not _NO_EXPECTATION and \
            current_identity != expected_identity:
        raise StaleTargetError("%s changed after it was read" % path)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    replaced = False
    try:
        try:
            try:
                os.fchmod(fd, target_mode)
            except (AttributeError, OSError):
                pass
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
            fd = None
        if expected_identity is not _NO_EXPECTATION:
            if path.exists():
                latest_identity = _identity(os.lstat(str(path)))
            else:
                latest_identity = _EXPECTED_MISSING
            if latest_identity != expected_identity:
                raise StaleTargetError(
                    "%s changed during rewrite" % path)
        for attempt in range(200):
            try:
                os.replace(tmp, str(path))
                replaced = True
                break
            except PermissionError:
                if os.name != "nt" or attempt == 199:
                    raise
                time.sleep(0.05)
    finally:
        if fd is not None:
            os.close(fd)
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@contextmanager
def _exclusive_file_lock(path, boundary, timeout=LOCK_TIMEOUT_SECONDS):
    """Portable advisory lock for the complete read-plan-commit cycle."""
    fd = _open_regular(
        path, os.O_RDWR | os.O_CREAT, boundary=boundary)
    lockf = os.fdopen(fd, "r+b", buffering=0)
    backend = None
    try:
        if os.fstat(lockf.fileno()).st_size == 0:
            lockf.write(b"\0")
            lockf.flush()
            os.fsync(lockf.fileno())
        try:
            import fcntl
        except ImportError:
            try:
                import msvcrt
            except ImportError:
                raise RuntimeError("no supported file-lock backend")
            deadline = time.monotonic() + timeout
            mode = getattr(msvcrt, "LK_NBLCK", msvcrt.LK_LOCK)
            while True:
                lockf.seek(0)
                try:
                    msvcrt.locking(lockf.fileno(), mode, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "could not acquire %s within %.1f seconds"
                            % (path, timeout))
                    time.sleep(0.05)
            backend = ("msvcrt", msvcrt)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(
                        lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "could not acquire %s within %.1f seconds"
                            % (path, timeout))
                    time.sleep(0.05)
            backend = ("fcntl", fcntl)
        yield
    finally:
        if backend is not None:
            name, module = backend
            if name == "fcntl":
                module.flock(lockf.fileno(), module.LOCK_UN)
            else:
                lockf.seek(0)
                module.locking(lockf.fileno(), module.LK_UNLCK, 1)
        lockf.close()


def _unlink_regular(path, boundary, expected_identity=None):
    """Remove only the exact private regular file expected by the caller."""
    path = _absolute_path(path)
    boundary = _absolute_path(boundary)
    if os.name != "nt" and os.unlink in getattr(
            os, "supports_dir_fd", set()):
        relative = path.relative_to(boundary)
        dir_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
        parent_fd = os.open(str(boundary), dir_flags)
        try:
            for part in relative.parent.parts:
                next_fd = os.open(part, dir_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            info = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UnsafePathError(
                    "refusing to remove unsafe file %s" % path)
            if expected_identity is not None and \
                    _identity(info) != expected_identity:
                raise StaleTargetError(
                    "%s changed before removal" % path)
            os.unlink(path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return
    _reject_symlinked_parents(path, boundary)
    info = os.lstat(str(path))
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UnsafePathError(
            "refusing to remove unsafe file %s" % path)
    if expected_identity is not None and _identity(info) != expected_identity:
        raise StaleTargetError("%s changed before removal" % path)
    for attempt in range(200):
        try:
            os.unlink(str(path))
            return
        except PermissionError:
            if os.name != "nt" or attempt == 199:
                raise
            time.sleep(0.05)


_TERMINAL_CONTROLS = re.compile(
    u"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    u"\u202a-\u202e\u2066-\u2069\ud800-\udfff]")


def _terminal_text(value):
    """Preserve layout while removing terminal and bidi control codes."""
    return _TERMINAL_CONTROLS.sub("", str(value))


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
            elif ln.lstrip().startswith("#"):
                # a section header is a structural boundary: flush the current
                # bullet and keep the header as its own standalone entry so it
                # is never absorbed as a continuation line and can't be
                # archived as dedup side-cargo (auditor finding)
                if cur:
                    entries.append("\n".join(cur).strip())
                    cur = []
                entries.append(ln.strip())
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
    __slots__ = ("text", "toks", "pos", "is_header")

    def __init__(self, text, pos, is_header=False):
        self.text = text
        self.toks = tokens(text)
        self.pos = pos          # original position: later = newer (append-log)
        # a markdown section header is structure, not a memory: never a
        # dedup/supersession candidate, re-emitted verbatim in place. This is
        # ONLY meaningful in bullets mode — in sections/paragraphs a leading
        # '#' is ordinary entry content, so is_header must be gated on the
        # format by the caller (auditor finding: computing it globally
        # wrongly exempted Hermes `sections` entries that start with '#').
        self.is_header = is_header

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
        if len(entries) > MAX_ENTRIES:
            raise FileLimitError(
                "memory has %d entries; limit is %d"
                % (len(entries), MAX_ENTRIES))
        # only bullets mode has structural headers; in sections/paragraphs a
        # leading '#' is ordinary content
        hdr = fmt == "bullets"
        self.entries = [Entry(t, i, is_header=(hdr and t.lstrip().startswith("#")))
                        for i, t in enumerate(entries)]
        self.fmt = fmt
        self.opts = opts
        self.actions = []
        self.flags = []       # journal-only observations
        self.themes = []
        self.comparisons = 0

    def _spend_comparisons(self, count=1):
        if self.comparisons + count > MAX_DREAM_COMPARISONS:
            raise FileLimitError(
                "dream comparison budget exceeded (%d)"
                % MAX_DREAM_COMPARISONS)
        self.comparisons += count

    def _pair_scores(self, a, b):
        self._spend_comparisons()
        return jaccard(a, b), containment(a, b)

    def _pair_jaccard(self, a, b):
        self._spend_comparisons()
        return jaccard(a, b)

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
            if e.is_header:          # structure, never a dedup candidate
                kept.append(e)
                continue
            dup_of = None
            dup_score = 0.0
            for k in kept:
                if k.is_header:
                    continue
                jac, con = self._pair_scores(e.toks, k.toks)
                if jac >= NEAR_DUP_JACCARD or con >= NEAR_DUP_CONTAINMENT:
                    # Same token BAG isn't the same fact if the words are in a
                    # different ORDER: "A calls B" vs "B calls A", "prefers
                    # tabs over spaces" vs "prefers spaces over tabs" are
                    # opposites, not duplicates. Require the near-identical
                    # pair to share the same token SEQUENCE before merging;
                    # order-reversed pairs fall through to the deep-sleep
                    # conflict scan instead (auditor finding).
                    if _same_sequence(e.toks, k.toks):
                        dup_of = k
                        dup_score = jac
                        break
            if dup_of is None:
                kept.append(e)
            else:
                # keep whichever says more; on a tie keep the LATER entry
                # (the file is an append log, later = newer — consistent with
                # supersession's newest-wins rule; the old tie-break kept the
                # older entry and could revert a correction — auditor finding)
                if len(e.text) > len(dup_of.text) or (
                        len(e.text) == len(dup_of.text) and e.pos > dup_of.pos):
                    rich, poor = e, dup_of
                    kept[kept.index(dup_of)] = e
                else:
                    rich, poor = dup_of, e
                self.actions.append(Action(
                    "light", "near-duplicate",
                    "same fact worded twice (%.0f%% token overlap); kept the richer wording"
                    % (dup_score * 100),
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
                if a.is_header or b.is_header:   # never supersede structure
                    continue
                if a.eid in removed or b.eid in removed:
                    continue
                self._spend_comparisons()
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
            if e.is_header:          # structure never ages out
                kept.append(e)
                continue
            entry_state = state.get(e.eid, {})
            first_seen = (
                entry_state.get("first_seen")
                if isinstance(entry_state, dict) else None)
            if isinstance(first_seen, str) and first_seen:
                try:
                    age = (now - datetime.fromisoformat(first_seen)).days
                except (TypeError, ValueError):
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
                        if es[i].is_header or es[j].is_header:
                            continue     # never merge a structural header
                        sim = self._pair_jaccard(
                            es[i].toks, es[j].toks)
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
        # step 2: archive the most redundant entries until we fit (never a
        # header — structure isn't budget ballast)
        while content_size([e.text for e in self.entries], self.fmt) > budget:
            candidates = [e for e in self.entries if not e.is_header]
            if len(candidates) <= 1:
                break
            self._spend_comparisons(len(candidates))
            frequencies = Counter()
            for entry in candidates:
                frequencies.update(set(entry.toks))
            victim = min(
                candidates,
                key=lambda entry: self._info_density(
                    entry, frequencies))
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

    def _info_density(self, entry, frequencies=None):
        if frequencies is None:
            frequencies = Counter()
            for candidate in self.entries:
                frequencies.update(set(candidate.toks))
        unique = sum(
            1 for token in set(entry.toks)
            if frequencies[token] == 1)
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


def archive_path(target):
    return target.parent / ("%s.dream-archive.md" % target.name)


def journal_path(target):
    return target.parent / "DREAMS.md"


def pending_path(target):
    return target.parent / (".%s.dream-pending.json" % target.name)


def lock_path(target):
    # Shared by every target in this directory because DREAMS.md is shared.
    return target.parent / ".dream.lock"


def _load_state_snapshot(target):
    p = state_path(target)
    raw, identity = _read_optional(
        p, max_bytes=MAX_SIDE_BYTES, with_identity=True,
        boundary=target.parent)
    if not raw:
        return {}, raw, identity
    try:
        state = json.loads(raw)
        if not isinstance(state, dict):
            state = {}
    except (json.JSONDecodeError, RecursionError, ValueError):
        state = {}
    return state, raw, identity


def load_state(target):
    target = _absolute_path(target)
    state, _, _ = _load_state_snapshot(target)
    return state


def _state_text(entries, old_state):
    now = _now().isoformat(timespec="seconds")
    if not isinstance(old_state, dict):
        old_state = {}
    st = {}
    for e in entries:
        prev = old_state.get(e.eid, {})
        if not isinstance(prev, dict):
            prev = {}
        first_seen = prev.get("first_seen")
        if not isinstance(first_seen, str):
            first_seen = now
        runs_seen = prev.get("runs_seen", 0)
        if isinstance(runs_seen, bool) or not isinstance(runs_seen, int) or \
                runs_seen < 0:
            runs_seen = 0
        st[e.eid] = {
            "first_seen": first_seen,
            "last_seen": now,
            "runs_seen": runs_seen + 1,
            "preview": _short(e.text, 60),
        }
    return json.dumps(st, ensure_ascii=False, indent=1)


def save_state(target, entries, old_state):
    target = _absolute_path(target)
    path = state_path(target)
    _, identity = _read_optional(
        path, max_bytes=MAX_SIDE_BYTES, with_identity=True,
        boundary=target.parent)
    _atomic_write(
        path, _state_text(entries, old_state), boundary=target.parent,
        expected_identity=identity)


def _archive_block(actions, stamp):
    lines = []
    for action in actions:
        if action.removed:
            lines.append(
                "## %s — %s (%s)\n\n%s\n"
                % (stamp, action.kind, action.reason, action.removed))
    return "\n".join(lines)


def _append_marked_file(path, header, marker, block, max_bytes,
                        rotate=False):
    """Append idempotently under the directory lock using atomic rewrite."""
    boundary = path.parent
    for attempt in range(20):
        previous, identity = _read_optional(
            path, max_bytes=max_bytes, with_identity=True,
            boundary=boundary)
        if marker in previous:
            return path
        base = previous or header
        if base and not base.endswith("\n"):
            base += "\n"
        text = base + marker + "\n" + block.rstrip() + "\n"
        if len(text.encode("utf-8")) > max_bytes:
            if not rotate:
                raise FileLimitError(
                    "%s exceeds the %d-byte limit" % (path, max_bytes))
            text = (header.rstrip() + " (rotated)\n\n" + marker + "\n" +
                    block.rstrip() + "\n")
            if len(text.encode("utf-8")) > max_bytes:
                raise FileLimitError(
                    "one journal record exceeds the %d-byte limit"
                    % max_bytes)
        try:
            _atomic_write(
                path, text, boundary=boundary,
                expected_identity=identity)
            return path
        except StaleTargetError:
            if attempt == 19:
                raise


def append_archive(target, actions, transaction_id=None):
    target = _absolute_path(target)
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    block = _archive_block(actions, stamp)
    if not block:
        return None
    txid = transaction_id or hashlib.sha256(
        block.encode("utf-8")).hexdigest()[:24]
    return _append_marked_file(
        archive_path(target),
        "# dream archive — nothing is ever deleted, only moved here\n\n",
        "<!-- dream-tx:%s -->" % txid,
        block, MAX_SIDE_BYTES)


def append_journal(target, report, transaction_id=None):
    target = _absolute_path(target)
    txid = transaction_id or hashlib.sha256(
        report.encode("utf-8")).hexdigest()[:24]
    return _append_marked_file(
        journal_path(target),
        "# DREAMS.md — dream journal\n\n",
        "<!-- dream-tx:%s -->" % txid,
        report, JOURNAL_MAX_BYTES, rotate=True)


def _preflight_artifact(path, max_bytes):
    """Validate an existing destination without creating anything."""
    boundary = path.parent
    try:
        _read_text_retry(
            path, max_bytes=max_bytes, boundary=boundary)
        return
    except FileNotFoundError:
        pass
    _reject_symlinked_parents(path, boundary)
    if os.name != "nt":
        flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                 getattr(os, "O_NOFOLLOW", 0))
        fd = os.open(str(boundary), flags)
        os.close(fd)


def _transaction_id(original, new_text, report, state_after,
                    archive="", state_before=""):
    payload = "\0".join((
        original, new_text, archive, report, state_before, state_after))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _build_transaction(target, plan):
    archive = _archive_block(
        plan["dreamer"].actions, plan["stamp"])
    txid = _transaction_id(
        plan["original"], plan["new_text"], plan["report"],
        plan["state_after"], archive=archive,
        state_before=plan["state_raw"])
    backup = "%s.bak-dream-%s-%s" % (
        target.name, plan["stamp"].replace("-", "").replace(":", "").replace(
            " ", "-"), txid[:8])
    return {
        "version": 1,
        "target": target.name,
        "tx": txid,
        "backup": backup,
        "original": plan["original"],
        "new_text": plan["new_text"],
        "archive": archive,
        "report": plan["report"],
        "state_before": plan["state_raw"],
        "state_after": plan["state_after"],
    }


def _validate_transaction(target, tx):
    required = {
        "version": int, "target": str, "tx": str, "backup": str,
        "original": str, "new_text": str, "archive": str,
        "report": str, "state_before": str, "state_after": str,
    }
    if not isinstance(tx, dict):
        raise ValueError("pending transaction is not an object")
    for key, kind in required.items():
        value = tx.get(key)
        valid = type(value) is int if kind is int else isinstance(value, kind)
        if not valid:
            raise ValueError(
                "pending transaction has an invalid %s" % key)
    if tx["version"] != 1 or tx["target"] != target.name:
        raise ValueError("pending transaction belongs to another target")
    if not re.fullmatch(r"[0-9a-f]{24}", tx["tx"]):
        raise ValueError("pending transaction id is invalid")
    if Path(tx["backup"]).name != tx["backup"] or not tx["backup"].startswith(
            target.name + ".bak-dream-") or not tx["backup"].endswith(
                "-" + tx["tx"][:8]):
        raise ValueError("pending backup name is invalid")
    if len(tx["original"].encode("utf-8")) > MAX_TARGET_BYTES or \
            len(tx["new_text"].encode("utf-8")) > MAX_TARGET_BYTES:
        raise FileLimitError("pending transaction target is too large")
    if len(tx["archive"].encode("utf-8")) > MAX_SIDE_BYTES or \
            len(tx["report"].encode("utf-8")) > JOURNAL_MAX_BYTES or \
            len(tx["state_before"].encode("utf-8")) > MAX_SIDE_BYTES or \
            len(tx["state_after"].encode("utf-8")) > MAX_SIDE_BYTES:
        raise FileLimitError("pending transaction side data is too large")
    expected = _transaction_id(
        tx["original"], tx["new_text"], tx["report"], tx["state_after"],
        archive=tx["archive"], state_before=tx["state_before"])
    if tx["tx"] != expected:
        raise ValueError("pending transaction checksum does not match")


def _ensure_backup(target, tx):
    path = target.parent / tx["backup"]
    existing, identity = _read_optional(
        path, max_bytes=MAX_TARGET_BYTES, with_identity=True,
        boundary=target.parent)
    if identity is _EXPECTED_MISSING:
        _atomic_write(
            path, tx["original"], boundary=target.parent,
            expected_identity=_EXPECTED_MISSING)
    elif existing != tx["original"]:
        raise StaleTargetError(
            "backup path contains different data: %s" % path)
    return path


def _commit_target(target, tx):
    current, identity = _read_text_retry(
        target, max_bytes=MAX_TARGET_BYTES, with_identity=True,
        boundary=target.parent)
    if current == tx["new_text"]:
        return
    if current != tx["original"]:
        raise StaleTargetError(
            "%s changed outside dream; refusing to overwrite it" % target)
    _atomic_write(
        target, tx["new_text"], boundary=target.parent,
        expected_identity=identity)


def _commit_state(target, tx):
    path = state_path(target)
    current, identity = _read_optional(
        path, max_bytes=MAX_SIDE_BYTES, with_identity=True,
        boundary=target.parent)
    if current == tx["state_after"]:
        return
    if current != tx["state_before"]:
        raise StaleTargetError(
            "%s changed outside dream; refusing to overwrite it" % path)
    _atomic_write(
        path, tx["state_after"], boundary=target.parent,
        expected_identity=identity)


def _recover_pending(target):
    """Finish an interrupted apply. Every step is idempotent."""
    path = pending_path(target)
    raw, pending_identity = _read_optional(
        path, max_bytes=MAX_PENDING_BYTES, with_identity=True,
        boundary=target.parent)
    if pending_identity is _EXPECTED_MISSING:
        return False
    try:
        tx = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(
            "pending transaction is corrupt: %s" % exc)
    _validate_transaction(target, tx)
    marker = "<!-- dream-tx:%s -->" % tx["tx"]
    _ensure_backup(target, tx)
    if tx["archive"]:
        _append_marked_file(
            archive_path(target),
            "# dream archive — nothing is ever deleted, only moved here\n\n",
            marker, tx["archive"], MAX_SIDE_BYTES)
    # Removed entries are durable before the live file is consolidated.
    _commit_target(target, tx)
    _append_marked_file(
        journal_path(target),
        "# DREAMS.md — dream journal\n\n",
        marker, tx["report"], JOURNAL_MAX_BYTES, rotate=True)
    _commit_state(target, tx)
    _, latest_identity = _read_text_retry(
        path, max_bytes=MAX_PENDING_BYTES, with_identity=True,
        boundary=target.parent)
    if latest_identity != pending_identity:
        raise StaleTargetError(
            "pending transaction changed during recovery")
    _unlink_regular(
        path, target.parent, expected_identity=latest_identity)
    return True


def _build_plan(target, opts):
    original, target_identity = _read_text_retry(
        target, max_bytes=MAX_TARGET_BYTES, with_identity=True,
        boundary=target.parent)
    fmt = opts.get("format") or detect_format(original)
    preamble, entries = parse(original, fmt)
    if not entries:
        return {
            "empty": True, "original": original, "format": fmt,
            "target_identity": target_identity,
        }

    state, state_raw, state_identity = _load_state_snapshot(target)
    dreamer = Dreamer(entries, fmt, opts)
    dreamer.light_sleep()
    dreamer.deep_sleep()
    dreamer.age_out(state, opts.get("max_age"))
    dreamer.rem()
    dreamer.squeeze(opts.get("budget"))

    new_entries = [
        entry.text for entry in sorted(
            dreamer.entries, key=lambda entry: entry.pos)]
    new_text = serialize(preamble, new_entries, fmt)
    size_before = content_size(entries, fmt)
    size_after = content_size(new_entries, fmt)
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    report = ["## dream — %s — %s" % (stamp, target.name), ""]
    report.append(
        "- entries: %d -> %d | chars: %d -> %d%s"
        % (len(entries), len(new_entries), size_before, size_after,
           (" | budget: %d" % opts["budget"])
           if opts.get("budget") else ""))
    if dreamer.actions:
        report.append("")
        for action in dreamer.actions:
            report.append(
                "- " + action.describe().replace("\n", "\n  "))
    else:
        report.append(
            "- memory is already clean: no duplicates, no supersessions.")
    if dreamer.flags:
        report.append("\n### flags (no action taken)")
        for flag in dreamer.flags:
            report.append("- " + flag.replace("\n", "\n  "))
    if dreamer.themes:
        report.append("\n### recurring themes")
        report.append("- " + ", ".join(
            "%s (x%d)" % (theme, count)
            for theme, count in dreamer.themes))
    report_text = "\n".join(report)
    if len(report_text.encode("utf-8")) > JOURNAL_MAX_BYTES:
        raise FileLimitError(
            "dream report exceeds the %d-byte journal limit"
            % JOURNAL_MAX_BYTES)
    state_after = _state_text(dreamer.entries, state)
    if len(state_after.encode("utf-8")) > MAX_SIDE_BYTES:
        raise FileLimitError(
            "dream state exceeds the %d-byte limit" % MAX_SIDE_BYTES)
    return {
        "empty": False,
        "original": original,
        "target_identity": target_identity,
        "format": fmt,
        "entries": entries,
        "new_entries": new_entries,
        "new_text": new_text,
        "size_before": size_before,
        "size_after": size_after,
        "stamp": stamp,
        "report": report_text,
        "changed": new_text.strip() != original.strip(),
        "dreamer": dreamer,
        "state": state,
        "state_raw": state_raw,
        "state_identity": state_identity,
        "state_after": state_after,
    }


def _show_dry_run(target, plan, quiet):
    if not quiet:
        print(_terminal_text(plan["report"]))
        if plan["changed"]:
            print("\n--- diff preview ---")
            for line in difflib.unified_diff(
                    plan["original"].splitlines(),
                    plan["new_text"].splitlines(),
                    fromfile=str(target),
                    tofile=str(target) + " (after dream)",
                    lineterm=""):
                print(_terminal_text(line))
            print("\ndry run — nothing written. add --apply to consolidate.")
        else:
            print("\nno changes needed.")
    else:
        print("%s: %d -> %d entries, %d -> %d chars (dry run)"
              % (target.name, len(plan["entries"]),
                 len(plan["new_entries"]), plan["size_before"],
                 plan["size_after"]))


def _apply_plan(target, plan, quiet):
    for path, limit in (
            (archive_path(target), MAX_SIDE_BYTES),
            (journal_path(target), JOURNAL_MAX_BYTES),
            (state_path(target), MAX_SIDE_BYTES),
            (pending_path(target), MAX_PENDING_BYTES)):
        _preflight_artifact(path, limit)

    if plan["changed"]:
        tx = _build_transaction(target, plan)
        _validate_transaction(target, tx)
        backup = target.parent / tx["backup"]
        _preflight_artifact(backup, MAX_TARGET_BYTES)
        pending = json.dumps(
            tx, ensure_ascii=False, sort_keys=True)
        if len(pending.encode("utf-8")) > MAX_PENDING_BYTES:
            raise FileLimitError(
                "pending transaction exceeds %d bytes"
                % MAX_PENDING_BYTES)
        _atomic_write(
            pending_path(target), pending, boundary=target.parent,
            expected_identity=_EXPECTED_MISSING)
        _recover_pending(target)
        print("%s consolidated: %d -> %d entries, %d -> %d chars"
              % (target.name, len(plan["entries"]),
                 len(plan["new_entries"]), plan["size_before"],
                 plan["size_after"]))
        if not quiet:
            print("  backup:  %s" % backup.name)
            if tx["archive"]:
                print("  archive: %s (every removed entry, with reasons)"
                      % archive_path(target).name)
            print("  journal: %s" % journal_path(target).name)
        return

    txid = _transaction_id(
        plan["original"], plan["new_text"], plan["report"],
        plan["state_after"])
    append_journal(target, plan["report"], transaction_id=txid)
    _atomic_write(
        state_path(target), plan["state_after"], boundary=target.parent,
        expected_identity=plan["state_identity"])
    print("%s: already clean, nothing to change." % target.name)


# ────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────
def dream_file(target, opts):
    target = _absolute_path(target)
    if not os.path.lexists(str(target)):
        print("error: %s does not exist" % target, file=sys.stderr)
        return 1
    try:
        if opts.get("apply"):
            with _exclusive_file_lock(
                    lock_path(target), target.parent):
                _recover_pending(target)
                plan = _build_plan(target, opts)
                if plan["empty"]:
                    print("%s: no entries found (format: %s) — nothing to do."
                          % (target.name, plan["format"]))
                    return 0
                _apply_plan(target, plan, opts.get("quiet"))
                return 0
        plan = _build_plan(target, opts)
        if plan["empty"]:
            print("%s: no entries found (format: %s) — nothing to do."
                  % (target.name, plan["format"]))
            return 0
        _show_dry_run(target, plan, opts.get("quiet"))
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print("error: %s" % _terminal_text(exc), file=sys.stderr)
        return 1


def hermes_targets():
    """Locate Hermes memory files + their configured char limits."""
    home = hermes_home()
    cfg = home / "config.yaml"
    mem_limit, user_limit = 2200, 1375       # Hermes defaults
    try:
        text = _read_text_retry(
            cfg, max_bytes=MAX_SIDE_BYTES, boundary=home)
    except FileNotFoundError:
        text = ""
    except (OSError, ValueError, UnicodeError):
        text = ""
    if text:
        try:
            m = re.search(r"memory_char_limit:\s*(\d+)", text)
            if m:
                mem_limit = int(m.group(1))
            m = re.search(r"user_char_limit:\s*(\d+)", text)
            if m:
                user_limit = int(m.group(1))
        except (ValueError, OverflowError):
            pass
    out = []
    for name, limit in (("MEMORY.md", mem_limit), ("USER.md", user_limit)):
        p = home / "memories" / name
        if p.exists():
            out.append((p, limit))
    return out


def hermes_home(environ=None, os_name=None, user_home=None):
    """Return the active Hermes profile directory on every platform."""
    environ = os.environ if environ is None else environ
    configured = environ.get("HERMES_HOME")
    if configured:
        return _absolute_path(configured)
    platform_name = os.name if os_name is None else os_name
    if platform_name == "nt":
        local = environ.get("LOCALAPPDATA")
        if local:
            return _absolute_path(Path(local) / "hermes")
    base = Path.home() if user_home is None else Path(user_home)
    return _absolute_path(base / ".hermes")


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
            if i >= len(argv):
                print("error: --format needs a value "
                      "(auto|sections|bullets|paragraphs)", file=sys.stderr)
                return 2
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
            home = hermes_home()
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
