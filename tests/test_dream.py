"""dream test suite — stdlib unittest only (zero dependencies, like the tool).

Run:  python3 -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dream as D                                     # noqa: E402
from dream import (Dreamer, Entry, detect_format, parse, serialize,  # noqa: E402
                   content_size, tokens, jaccard, _merge_texts,
                   dream_file, load_state, state_path, SECTION_DELIM)


def mk(entries, fmt="sections", opts=None):
    d = Dreamer(entries, fmt, opts or {})
    return d


class TmpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dream-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p


# ───────────────────────── format detection ─────────────────────────
class TestFormats(unittest.TestCase):
    def test_detect_sections(self):
        self.assertEqual(detect_format("fact one\n§\nfact two"), "sections")

    def test_detect_bullets(self):
        self.assertEqual(detect_format("# Memory\n\n- fact one\n- fact two\n"),
                         "bullets")

    def test_detect_paragraphs(self):
        self.assertEqual(detect_format("fact one alone\n\nfact two alone\n"),
                         "paragraphs")

    def test_sections_roundtrip(self):
        text = "fact one\n§\nfact two\n§\nmultiline\nfact three"
        pre, entries = parse(text, "sections")
        self.assertEqual(len(entries), 3)
        self.assertEqual(serialize(pre, entries, "sections"), text)

    def test_bullets_roundtrip_preserves_preamble(self):
        text = "# My memory\n\n- fact one\n- fact two\n  continuation\n"
        pre, entries = parse(text, "bullets")
        self.assertEqual(pre, "# My memory")
        self.assertEqual(len(entries), 2)
        self.assertIn("continuation", entries[1])
        out = serialize(pre, entries, "bullets")
        self.assertTrue(out.startswith("# My memory"))
        self.assertIn("- fact one", out)

    def test_hermes_size_accounting_matches_join(self):
        entries = ["abc", "defgh", "x"]
        expected = len(SECTION_DELIM.join(entries))
        self.assertEqual(content_size(entries, "sections"), expected)


# ───────────────────────── light sleep ─────────────────────────
class TestLightSleep(unittest.TestCase):
    def test_exact_duplicates_removed(self):
        d = mk(["user prefers dark mode", "user prefers dark mode"])
        d.light_sleep()
        self.assertEqual(len(d.entries), 1)
        self.assertEqual(d.actions[0].kind, "exact-duplicate")

    def test_normalized_duplicates_removed(self):
        d = mk(["User prefers DARK mode!", "user prefers dark mode"])
        d.light_sleep()
        self.assertEqual(len(d.entries), 1)

    def test_near_duplicates_keep_richer(self):
        short = "deploy needs the ssh key"
        rich = "deploy needs the ssh key stored in 1password vault"
        d = mk([short, rich])
        d.light_sleep()
        self.assertEqual(len(d.entries), 1)
        self.assertEqual(d.entries[0].text, rich)

    def test_distinct_facts_untouched(self):
        d = mk(["the api uses bearer tokens",
                "favorite color is green",
                "deploy target is hetzner"])
        d.light_sleep()
        self.assertEqual(len(d.entries), 3)
        self.assertEqual(d.actions, [])


# ───────────────────────── deep sleep ─────────────────────────
class TestDeepSleep(unittest.TestCase):
    def test_supersession_newest_wins(self):
        old = "project database is mysql version 5 on the old server"
        new = "project database is postgres version 16 on the new server"
        d = mk([old, new])
        d.light_sleep()
        d.deep_sleep()
        self.assertEqual(len(d.entries), 1)
        self.assertEqual(d.entries[0].text, new)
        self.assertEqual(d.actions[-1].kind, "superseded")

    def test_supersession_respects_file_order(self):
        new_first = ["project database is postgres version 16 on the new server",
                     "project database is mysql version 5 on the old server"]
        d = mk(new_first)
        d.light_sleep()
        d.deep_sleep()
        # later in file wins — file is an append log, so mysql (later) wins
        self.assertIn("mysql", d.entries[0].text)

    def test_no_supersede_flag(self):
        old = "project database is mysql version 5 on the old server"
        new = "project database is postgres version 16 on the new server"
        d = mk([old, new], opts={"no_supersede": True})
        d.light_sleep()
        d.deep_sleep()
        self.assertEqual(len(d.entries), 2)

    def test_unrelated_subjects_never_superseded(self):
        d = mk(["user timezone is UTC+3 riyadh",
                "api timeout is thirty seconds strict"])
        d.light_sleep()
        d.deep_sleep()
        self.assertEqual(len(d.entries), 2)

    def test_low_overlap_conflict_is_flagged_not_removed(self):
        a = "payment provider stripe fees two percent monthly billing plan"
        b = "payment provider paypal integration rejected last march audit"
        d = mk([a, b])
        d.light_sleep()
        d.deep_sleep()
        self.assertEqual(len(d.entries), 2, "uncertain conflicts must not be auto-resolved")


# ───────────────────────── squeeze / budget ─────────────────────────
class TestSqueeze(unittest.TestCase):
    def test_within_budget_untouched(self):
        d = mk(["short fact", "another fact"])
        d.squeeze(500)
        self.assertEqual(len(d.entries), 2)
        self.assertEqual(d.actions, [])

    def test_merge_preserves_novel_clauses(self):
        a = "deploy runs on hetzner. it needs the ssh key."
        b = "deploy runs on hetzner. rollback takes two minutes."
        d = mk([a, b])
        d.squeeze(80)   # under combined size (99) but room for one merged entry
        self.assertEqual(len(d.entries), 1)
        merged = d.entries[0].text
        self.assertIn("ssh", merged)
        self.assertIn("rollback", merged)

    def test_budget_is_enforced(self):
        entries = ["totally unique fact number %d about %s" % (i, w)
                   for i, w in enumerate(
                       ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])]
        budget = 120
        d = mk(entries)
        d.squeeze(budget)
        self.assertLessEqual(
            content_size([e.text for e in d.entries], "sections"), budget)
        self.assertTrue(any(a.kind == "archived-for-budget" for a in d.actions))

    def test_merge_texts_no_novel_returns_base(self):
        self.assertEqual(_merge_texts("a b c d", "a b c d"), "a b c d")

    def test_budget_guaranteed_on_unpunctuated_giant_entry(self):
        """Regression: one giant entry with no clause boundaries must still
        be forced under the budget (hard word-boundary cut, tail archived)."""
        giant = " ".join("token%d" % i for i in range(2000))   # no punctuation
        d = mk([giant])
        d.squeeze(2200)
        self.assertLessEqual(
            content_size([e.text for e in d.entries], "sections"), 2200)
        self.assertTrue(any(a.kind == "hard-truncated" for a in d.actions))

    def test_budget_guaranteed_at_tiny_budget(self):
        d = mk(["a fairly normal entry with a good number of words here"])
        d.squeeze(10)
        self.assertLessEqual(
            content_size([e.text for e in d.entries], "sections"), 10)


# ───────────────────────── file runner ─────────────────────────
class TestDreamFile(TmpTest):
    HERMES = ("user name is khaled from riyadh"
              + SECTION_DELIM +
              "project database is mysql version 5 on the old server"
              + SECTION_DELIM +
              "project database is postgres version 16 on the new server"
              + SECTION_DELIM +
              "user name is khaled from riyadh")

    def test_dry_run_by_default(self):
        p = self.write("MEMORY.md", self.HERMES)
        rc = dream_file(p, {"quiet": True})
        self.assertEqual(rc, 0)
        self.assertEqual(p.read_text("utf-8"), self.HERMES,
                         "dry run must not modify the file")
        self.assertFalse(list(self.tmp.glob("*.bak-dream-*")))

    def test_apply_writes_backup_archive_journal(self):
        p = self.write("MEMORY.md", self.HERMES)
        rc = dream_file(p, {"apply": True, "quiet": True})
        self.assertEqual(rc, 0)
        text = p.read_text("utf-8")
        self.assertEqual(text.count("khaled"), 1, "duplicate removed")
        self.assertNotIn("mysql", text, "superseded entry removed from file")
        self.assertTrue(list(self.tmp.glob("MEMORY.md.bak-dream-*")), "backup exists")
        archive = (self.tmp / "MEMORY.md.dream-archive.md").read_text("utf-8")
        self.assertIn("mysql", archive, "nothing is destroyed — archived instead")
        journal = (self.tmp / "DREAMS.md").read_text("utf-8")
        self.assertIn("superseded", journal)

    def test_apply_is_idempotent(self):
        p = self.write("MEMORY.md", self.HERMES)
        dream_file(p, {"apply": True, "quiet": True})
        first = p.read_text("utf-8")
        rc = dream_file(p, {"apply": True, "quiet": True})
        self.assertEqual(rc, 0)
        self.assertEqual(p.read_text("utf-8"), first,
                         "second dream on clean memory must change nothing")

    def test_budget_apply(self):
        entries = ["unique alpha fact %d wordy padding text here" % i
                   for i in range(10)]
        p = self.write("MEMORY.md", SECTION_DELIM.join(entries))
        dream_file(p, {"apply": True, "quiet": True, "budget": 150})
        _, after = parse(p.read_text("utf-8"), "sections")
        self.assertLessEqual(content_size(after, "sections"), 150)

    def test_bullets_file(self):
        p = self.write("notes.md",
                       "# Notes\n\n- fact one about api tokens\n"
                       "- fact one about api tokens\n- another topic entirely\n")
        dream_file(p, {"apply": True, "quiet": True})
        text = p.read_text("utf-8")
        self.assertEqual(text.count("fact one about api tokens"), 1)
        self.assertIn("# Notes", text, "preamble preserved")
        self.assertIn("another topic", text)

    def test_state_tracks_entries(self):
        p = self.write("MEMORY.md", self.HERMES)
        dream_file(p, {"apply": True, "quiet": True})
        st = load_state(p)
        self.assertTrue(st)
        for v in st.values():
            self.assertIn("first_seen", v)
            self.assertEqual(v["runs_seen"], 1)

    def test_max_age_archives_old_entries(self):
        p = self.write("MEMORY.md", "ancient fact about floppy disks"
                       + SECTION_DELIM + "fresh fact about ssd drives")
        dream_file(p, {"apply": True, "quiet": True})            # seed state
        st = load_state(p)
        old_iso = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
        for k in st:
            if "floppy" in st[k]["preview"]:
                st[k]["first_seen"] = old_iso
        state_path(p).write_text(json.dumps(st), encoding="utf-8")
        dream_file(p, {"apply": True, "quiet": True, "max_age": 30})
        text = p.read_text("utf-8")
        self.assertNotIn("floppy", text)
        self.assertIn("ssd", text)
        archive = (self.tmp / "MEMORY.md.dream-archive.md").read_text("utf-8")
        self.assertIn("floppy", archive)

    def test_missing_file(self):
        rc = dream_file(self.tmp / "nope.md", {})
        self.assertEqual(rc, 1)

    def test_empty_file(self):
        p = self.write("empty.md", "\n")
        rc = dream_file(p, {})
        self.assertEqual(rc, 0)

    def test_arabic_memory(self):
        text = ("المستخدم يفضل الوضع الداكن دائمًا"
                + SECTION_DELIM +
                "المستخدم يفضل الوضع الداكن دائمًا"
                + SECTION_DELIM +
                "قاعدة بيانات المشروع سيكلايت")
        p = self.write("MEMORY.md", text)
        dream_file(p, {"apply": True, "quiet": True})
        after = p.read_text("utf-8")
        self.assertEqual(after.count("الوضع الداكن"), 1)
        self.assertIn("سيكلايت", after)


# ───────────────────────── CLI ─────────────────────────
class TestCLI(TmpTest):
    def run_cli(self, *args):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = D.main(list(args))
        except SystemExit as e:
            code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_no_args_shows_error(self):
        code, _, err = self.run_cli("--quiet")
        self.assertEqual(code, 2)
        self.assertIn("no target", err)

    def test_help(self):
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("--apply", out)

    def test_unknown_option(self):
        code, _, err = self.run_cli("--frobnicate")
        self.assertEqual(code, 2)

    def test_budget_without_value_friendly_error(self):
        code, _, err = self.run_cli("x.md", "--budget")
        self.assertEqual(code, 2)
        self.assertIn("--budget", err)

    def test_budget_non_integer_friendly_error(self):
        code, _, err = self.run_cli("x.md", "--budget", "abc")
        self.assertEqual(code, 2)
        self.assertIn("integer", err)

    def test_budget_zero_rejected(self):
        code, _, err = self.run_cli("x.md", "--budget", "0")
        self.assertEqual(code, 2)

    def test_quiet_apply_prints_single_line(self):
        p = self.tmp / "m.md"
        p.write_text("dup fact here" + SECTION_DELIM + "dup fact here",
                     encoding="utf-8")
        code, out, _ = self.run_cli(str(p), "--apply", "--quiet")
        self.assertEqual(code, 0)
        self.assertEqual(len([ln for ln in out.splitlines() if ln.strip()]), 1)

    def test_dry_run_diff_output(self):
        p = self.tmp / "m.md"
        p.write_text("dup fact here" + SECTION_DELIM + "dup fact here",
                     encoding="utf-8")
        code, out, _ = self.run_cli(str(p))
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertIn("exact-duplicate", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
