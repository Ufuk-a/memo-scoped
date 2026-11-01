#!/usr/bin/env python3
"""Tests for memo. Run: python3 -m unittest -v test_memo

Every test gets a fresh temp store via $MEMORY_DIR."""

import contextlib
import json
import importlib.util
import io
import os
import math
import random
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "memo", os.path.join(HERE, "memo"),
    loader=importlib.machinery.SourceFileLoader("memo", os.path.join(HERE, "memo")))
memo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(memo)


def run(*argv, stdin=""):
    """Run a memo command in-process; returns (exit code, stdout)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv, sys.stdin = ["memo"] + list(argv), io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                memo.main()
            except SystemExit as e:
                code = e.code or 0
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin
    return code, out.getvalue() + (err.getvalue() if code else "")


def ok(*argv, **kw):
    code, out = run(*argv, **kw)
    if code:
        raise AssertionError("memo %s failed (%s):\n%s" % (argv, code, out))
    return out


def seed(d, n, scope="core", start=0, day=1):
    """Write n leaves straight into the journal with deterministic ids,
    timestamps and texts, so trees are reproducible across tests."""
    recs = []
    for k in range(n):
        i = start + k
        recs.append({"t": "note", "id": i, "ts": "2026-01-%02dT%02d:00:00" % (day + i // 24, i % 24),
                     "scope": scope, "text": "memory %d: fact number %d" % (i, i * 7)})
    memo.append_json(memo.log_path(d), recs)
    return recs


def ancestors(tree, leaf_hash):
    """Node hashes on the path from a leaf to the root."""
    parent = {c: n.hash for n in tree.nodes.values() for c in n.children}
    out, h = set(), leaf_hash
    while h in parent:
        h = parent[h]
        out.add(h)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="memotest.")
        os.environ["MEMORY_DIR"] = self.d
        self.cwd = os.getcwd()
        ok("init")
        memo.config(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)  # a temp dir this test created
        for k, (default, _) in memo.KNOBS.items():
            setattr(memo, k, default)

    def model(self):
        return memo.Model(self.d)

    def open_root_scope(self, scope, start):
        """Seed leaves until the scope's root is an open node."""
        n = 0
        while True:
            seed(self.d, 1, scope=scope, start=start + n)
            n += 1
            t = self.model().trees[scope]
            if t.root in t.nodes and not t.nodes[t.root].complete and n > 20:
                return t



class TestNote(Base):
    def test_scope_resolution(self):
        out = ok("note", "plain")
        self.assertIn("Saved as #0 in scope core.", out)
        out = ok("note", "scoped", "--scope", "infra/backup")
        self.assertIn("in scope infra/backup", out)
        proj = os.path.join(self.d, "proj")
        os.makedirs(os.path.join(proj, "sub"))
        with open(os.path.join(self.d, "scopes.map"), "a") as f:
            f.write("%s  proj\n%s  proj/sub\n" % (proj, os.path.join(proj, "sub")))
        os.chdir(proj)
        self.assertIn("in scope proj.", ok("note", "from cwd"))
        os.chdir(os.path.join(proj, "sub"))
        self.assertIn("in scope proj/sub.", ok("note", "longest prefix wins"))
        self.assertIn("in scope core.", ok("note", "explicit", "--scope", "core"))
        m = self.model()
        self.assertEqual({l["scope"] for l in m.leaves.values()},
                         {"core", "infra/backup", "proj", "proj/sub"})
        code, out = run("note", "bad scope", "--scope", "Bad/Name")
        self.assertEqual(code, 1)
        code, out = run("note", "x" * 300)
        self.assertIn("Too long", out)
        code, out = run("note", "two\nlines")
        self.assertEqual(code, 1)
        self.assertEqual(self.model().next_id, 5)

    def test_wake_parts(self):
        seed(self.d, 60)
        whole = ok("wake")
        parts = [ok("wake", "--part", "%d/3" % k) for k in (1, 2, 3)]
        # Every line exactly once, in order; the slices are near-equal.
        self.assertEqual("".join(parts), whole)
        sizes = [len(p.encode()) for p in parts]
        self.assertLess(max(sizes) - min(sizes), 2 * memo.ENTRY_CHARS + 50)
        self.assertEqual(ok("wake", "--part", "1/1"), whole)
        # More parts than lines: empty parts print nothing at all.
        self.assertEqual("".join(ok("wake", "--part", "%d/500" % k)
                                 for k in range(1, 501)), whole)
        for bad in ("0/3", "4/3", "x", "1/0"):
            self.assertEqual(run("wake", "--part", bad)[0], 1)
        self.assertEqual(run("wake", "--part")[0], 1)

    def test_wake_of_cwd_scopes(self):
        proj = os.path.join(self.d, "proj")
        os.makedirs(proj)
        with open(os.path.join(self.d, "scopes.map"), "a") as f:
            f.write("~/definitely/not/here  nope\n%s  proj\n" % proj)
        seed(self.d, 3)
        seed(self.d, 3, scope="proj", start=3)
        seed(self.d, 3, scope="other", start=6)
        os.chdir(proj)
        out = ok("wake")
        self.assertIn("== core (3 memories)", out)
        self.assertIn("== proj (3 memories)", out)
        self.assertNotIn("== other", out)
        self.assertIn("other (3): (no summary yet)", out)
        self.assertTrue(out.rstrip().endswith("You are awake."))
        out = ok("wake", "other")
        self.assertIn("== other (3 memories)", out)


    def test_map(self):
        proj = os.path.join(self.d, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        self.assertIn("No folder", ok("map"))
        self.assertIn("Linked", ok("map", "proj"))
        self.assertIn("already linked", ok("map", "proj", proj))
        ok("note", "a proj fact")
        self.assertEqual(self.model().leaves[0]["scope"], "proj")
        self.assertIn("== proj (1 memory)", ok("wake"))
        self.assertIn("<- here", ok("map"))
        # Relink replaces in place (no second line); comments survive.
        self.assertIn("(was proj)", ok("map", "other", "."))
        with open(os.path.join(self.d, "scopes.map")) as f:
            text = f.read()
        self.assertEqual(text.count(proj), 1)
        self.assertIn("# cwd -> scope", text)
        self.assertIn("other", text)
        # A relative PATH resolves against here; ~ and / are refused.
        ok("map", "sub", "deeper")
        self.assertIn(os.path.join(proj, "deeper"), ok("map").replace("~", os.path.expanduser("~")))
        self.assertEqual(run("map", "x", "~")[0], 1)
        self.assertEqual(run("map", "x", "/")[0], 1)
        self.assertEqual(run("map", "Bad Scope")[0], 1)
        self.assertEqual(run("map", "a", "b", "c")[0], 1)
        # --rm; a second --rm is an error.
        self.assertIn("Unlinked", ok("map", "--rm"))
        self.assertEqual(run("map", "--rm")[0], 1)
        self.assertIn("Unlinked", ok("map", "--rm", "deeper"))

    def test_memo_cwd_overrides_cwd(self):
        proj = os.path.join(self.d, "proj")
        os.makedirs(proj)
        with open(os.path.join(self.d, "scopes.map"), "a") as f:
            f.write("%s  proj\n" % proj)
        seed(self.d, 2, scope="proj")
        os.environ["MEMO_CWD"] = proj
        try:
            self.assertIn("== proj (2 memories)", ok("wake"))
        finally:
            del os.environ["MEMO_CWD"]
        self.assertNotIn("== proj", ok("wake"))


class TestTree(Base):
    def test_chunk_determinism(self):
        recs = seed(self.d, 60)
        a = self.model().trees["core"]
        d2 = tempfile.mkdtemp(prefix="memotest.")
        try:
            random.Random(7).shuffle(recs)
            memo.append_json(memo.log_path(d2), recs)
            b = memo.Model(d2).trees["core"]
        finally:
            shutil.rmtree(d2)
        self.assertEqual(a.root, b.root)
        self.assertEqual(set(a.nodes), set(b.nodes))
        self.assertGreater(len(a.nodes), 5)
        # the tree covers every leaf exactly once
        self.assertEqual(a.leaf_set(a.root), set(range(60)))
        # complete = closed by the hash rule, and not holding the open tail
        for n in a.nodes.values():
            last = n.children[-1]
            self.assertGreater(len(n.children), 1)
            self.assertEqual(n.complete, memo.closes_chunk(last) and (
                a.is_leaf(last) or a.nodes[last].complete))
            # inner items never close: a singleton joins the next chunk
            for c in n.children[1:-1]:
                self.assertFalse(memo.closes_chunk(c))
        # an open node's parent is open too: the tail is raw all the way up
        for n in a.nodes.values():
            for c in n.children:
                if c in a.nodes and not a.nodes[c].complete:
                    self.assertFalse(n.complete)

    def test_locality_of_edit(self):
        seed(self.d, 80)
        old = self.model().trees["core"]
        # Content-defined chunking: an edit changes the leaf's ancestors, and
        # may also flip whether an ancestor closes its chunk, which moves a
        # boundary. Pick an edit that flips nothing, and expect exactly the
        # ancestors to change.
        leaves = self.model().leaves
        for i in range(0, 40):
            leaf = dict(leaves[i])
            oh = memo.leaf_hash(leaf)
            for k in range(50):
                leaf["text"] = "rewritten %d variant %d" % (i, k)
                trial = list(leaves.values())
                trial[i] = leaf
                new = memo.Tree("core", trial)
                if (set(old.nodes) - set(new.nodes) == ancestors(old, oh) and
                        set(new.nodes) - set(old.nodes) == ancestors(new, memo.leaf_hash(leaf))):
                    break
            else:
                continue
            break
        else:
            self.fail("no boundary-preserving edit found")
        ok("edit", str(i), leaf["text"])
        new = self.model().trees["core"]
        nh = memo.leaf_hash(leaf)
        self.assertNotEqual(oh, nh)
        self.assertEqual(set(old.nodes) - set(new.nodes), ancestors(old, oh))
        self.assertEqual(set(new.nodes) - set(old.nodes), ancestors(new, nh))
        self.assertNotEqual(old.root, new.root)

    def test_move_disjoint_ranges(self):
        seed(self.d, 100)
        old = self.model().trees["core"]
        # ranges that are whole level-1 chunks: from a chunk start to a
        # chunk end. Level-1 nodes with complete=True are such chunks.
        # (single-leaf chunks are skipped: as the last item of the target's
        # level 0 such a leaf is carried up unchanged, not wrapped in a node)
        chunks = sorted((n for n in old.nodes.values()
                         if n.level == 1 and n.complete and len(n.children) > 1),
                        key=lambda n: n.seq)
        picked = [chunks[1], chunks[3]]
        ids = sorted(set().union(*(old.leaf_set(n.hash) for n in picked)))
        spec = ",".join("%d-%d" % (min(old.leaf_set(n.hash)), max(old.leaf_set(n.hash)))
                        for n in picked)
        self.assertLess(len(ids), 100)
        out = ok("move", spec, "--to", "proj", "--yes")
        self.assertIn("Moved %d memories to scope proj." % len(ids), out)
        m = self.model()
        src, dst = m.trees["core"], m.trees["proj"]
        self.assertEqual(dst.leaf_set(dst.root), set(ids))
        self.assertEqual(src.leaf_set(src.root), set(range(100)) - set(ids))
        # target reuses the moved chunks as they were in the source
        for n in picked:
            self.assertIn(n.hash, dst.nodes)
            self.assertEqual(dst.nodes[n.hash].children, n.children)
        # everything strictly before the first moved leaf is unchanged
        first = min(ids)
        before = [n.hash for n in old.nodes.values()
                  if max(old.leaf_set(n.hash)) < first]
        self.assertGreater(len(before), 0)
        for h in before:
            self.assertIn(h, src.nodes)
        # a second move of the same ids is a no-op
        self.assertIn("Nothing to move", ok("move", spec, "--from", "core", "--to", "proj", "--yes"))
        # --match with confirmation refused
        out = ok("move", "--match", "fact number 7$", "--to", "x", stdin="n\n")
        self.assertIn("Not moved", out)
        self.assertNotIn("x", self.model().trees)
        out = ok("move", "--match", "fact number 7$", "--from", "core", "--to", "x", stdin="y\n")
        self.assertEqual(self.model().leaves[1]["scope"], "x")

    def test_drop_and_recall_all(self):
        seed(self.d, 5)
        ok("drop", "1,3")
        m = self.model()
        self.assertEqual(m.count("core"), 3)
        self.assertNotIn("#1 ", ok("recall", "memory"))
        self.assertIn("(dropped) #1 ", ok("recall", "memory", "--all"))
        self.assertIn("Already dropped", ok("drop", "1"))


class TestWake(Base):
    def wake(self, *scopes):
        out = ok("wake", *scopes)
        self.assertTrue(out.rstrip().endswith("You are awake."))
        return out

    def test_budget(self):
        seed(self.d, 120)
        seed(self.d, 120, scope="a", start=120)
        seed(self.d, 120, scope="b", start=240)
        m = self.model()
        # settle every pending nap so the frontier is free to choose
        for _ in range(500):
            p = m.pending()
            if not p:
                break
            s, h = p[0]
            memo.append_json(memo.summaries_path(self.d), [{"node": h, "text": "sum " + h[:6]}])
            m = self.model()
        self.assertFalse(m.pending())
        ok("config", "WAKE_CHARS=3000", "CORE_CHARS=1200")
        memo.config(self.d)
        out = self.wake("a", "b")
        total = len(out.encode())
        self.assertLessEqual(total, 3000)
        self.assertIn("bytes: %d |" % total, out)
        core = out.split("== a (")[0]
        self.assertLessEqual(len(core.encode()), 1200)
        # core is served first: raising CORE_CHARS grows core's section
        ok("config", "CORE_CHARS=2000")
        memo.config(self.d)
        out2 = self.wake("a", "b")
        self.assertLessEqual(len(out2.encode()), 3000)
        self.assertGreater(len(out2.split("== a (")[0].encode()), len(core.encode()))
        # a summary line per cached node, leaf lines for the raw tail
        self.assertRegex(out, r"#[0-9a-f]{6} sum ")
        self.assertRegex(out, r"#\d+ 2026-01-\d\d memory")
        # old -> new, top to bottom, within a scope
        ids = [int(mm.group(1)) for mm in
               re.finditer(r"^#(\d+) 2026-", out.split("== a (")[1].split("== b (")[0], re.M)]
        self.assertEqual(ids, sorted(ids))

    def test_stale_and_dormant(self):
        seed(self.d, 40)
        seed(self.d, 2, scope="z", start=40)
        out = self.wake()
        self.assertIn("== core (40 memories) (stale)", out)
        self.assertIn("-- other scopes:", out)
        self.assertIn("z (2): (no summary yet)", out)
        ok("config", "DORMANT_LINES=0")
        memo.config(self.d)
        self.assertNotIn("-- other scopes:", self.wake())


class TestNaps(Base):
    def test_pending_order_and_prompt(self):
        seed(self.d, 60)
        m = self.model()
        t = m.trees["core"]
        pend = m.pending()
        self.assertTrue(pend)
        keys = [(t.nodes[h].level, t.nodes[h].seq) for _, h in pend]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0][0], 1)
        # a parent is never offered before its children are settled
        for _, h in pend:
            for c in t.nodes[h].children:
                self.assertTrue(t.is_leaf(c) or c in m.summaries)
        out = ok("nap")
        first = pend[0][1][:6]
        self.assertIn("Compress node #%s" % first, out)
        self.assertIn("nap %s \"<your line>\"" % first, out)
        for c in t.nodes[pend[0][1]].children:
            self.assertIn(m.line(c), out)
        out = ok("nap", first, "era one")
        self.assertIn("#%s saved." % first, out)
        m2 = self.model()
        self.assertEqual(m2.summaries[pend[0][1]], "era one")
        now = [h for _, h in m2.pending()]
        self.assertNotIn(pend[0][1], now)
        # settling a node may make its parent pending: nothing else changes
        parent = [n.hash for n in t.nodes.values() if pend[0][1] in n.children]
        self.assertEqual(set(now) - {h for _, h in pend}, set(parent) - {h for _, h in pend}
                         if all(t.is_leaf(c) or c in m2.summaries
                                for p in parent for c in t.nodes[p].children) else set())
        # bottom-up: answer everything, parents become pending as children settle
        n = 1
        while m2.pending(roots=True):
            s, h = m2.pending(roots=True)[0]
            ok("nap", h[:8], "s%d" % n)
            n += 1
            m2 = self.model()
        root_nap = 0 if t.nodes[t.root].complete else 1  # explicit nap offers the root
        self.assertEqual(n, len([x for x in t.nodes.values() if x.complete]) + root_nap)
        self.assertIn("Nothing left", ok("nap"))
        # the open tail refuses a summary; a leaf id too
        while not any(not x.complete and x.hash != t.root for x in t.nodes.values()):
            ok("note", "one more, so the tail is open")
            t = self.model().trees["core"]
        tail = next(x for x in t.nodes.values() if not x.complete and x.hash != t.root)
        code, out = run("nap", tail.hash[:8], "no")
        self.assertEqual(code, 1)
        code, out = run("nap", next(iter(t.leaves))[:8], "no")
        self.assertEqual(code, 1)

    def test_forget_tombstone(self):
        seed(self.d, 30)
        m = self.model()
        s, h = m.pending()[0]
        ok("nap", h[:6], "summary")
        self.assertNotIn(h, [x for _, x in self.model().pending()])
        out = ok("forget", h[:6])
        self.assertIn("Forgot #%s" % h[:6], out)
        m = self.model()
        self.assertNotIn(h, m.summaries)
        self.assertEqual(m.pending()[0], (s, h))
        # its parent, if it became pending, is not pending any more
        t = m.trees["core"]
        for n in t.nodes.values():
            if h in n.children:
                self.assertNotIn(n.hash, [x for _, x in m.pending()])
        with open(memo.summaries_path(self.d)) as f:
            lines = f.read().splitlines()
        self.assertIn('"text": null', lines[-1])
        self.assertEqual(len(lines), 2)  # append-only: nothing rewritten
        code, _ = run("forget", h[:6])
        self.assertEqual(code, 1)

    def test_note_prints_nap(self):
        for i in range(40):
            out = ok("note", "thing %d" % i)
        self.assertIn("Compress node #", out)
        self.assertIn("Run: ", out)


class TestJournal(Base):
    def test_crash_line(self):
        seed(self.d, 5)
        with open(memo.log_path(self.d), "ab") as f:
            f.write(b'{"t":"note","id":5,"ts":"2026-01-01T00:00:00","scope":"co')
        m = self.model()
        self.assertEqual(len(m.leaves), 5)
        self.assertTrue(m.truncated)
        out = ok("check")
        self.assertIn("truncated final journal line: yes", out)
        # the next append must not be glued to the remnant
        ok("note", "after the crash")
        m = self.model()
        # id 5 was mentioned by the remnant, so it is never reused
        self.assertEqual(sorted(m.leaves), [0, 1, 2, 3, 4, 6])
        self.assertEqual(m.leaves[6]["text"], "after the crash")
        self.assertFalse(m.truncated)
        self.assertEqual(len(m.malformed), 1)
        out = ok("check")
        self.assertIn("malformed journal lines: 1", out)
        self.assertIn("truncated final journal line: no", out)

    def test_replay_edit_move_drop(self):
        seed(self.d, 3)
        memo.append_json(memo.log_path(self.d), [
            {"t": "edit", "id": 1, "text": "new"},
            {"t": "move", "ids": [1, 2, 99], "scope": "p"},
            {"t": "drop", "ids": [2]},
            {"t": "bogus"},
            {"t": "note", "id": 0, "ts": "x", "scope": "core", "text": "dup id"},
        ])
        m = self.model()
        self.assertEqual(m.leaves[1]["text"], "new")
        self.assertEqual(m.leaves[1]["scope"], "p")
        self.assertTrue(m.leaves[2]["dropped"])
        self.assertEqual(m.leaves[0]["text"], "memory 0: fact number 0")
        self.assertEqual(len(m.malformed), 2)
        self.assertEqual(m.next_id, 3)
        self.assertEqual(sorted(m.trees), ["core", "p"])


class TestImportV1(Base):
    def test_import_synthetic_log(self):
        v1 = tempfile.mkdtemp(prefix="memov1.")
        try:
            os.makedirs(os.path.join(v1, "TREE"))
            texts = ["first thing", "second thing with ünïcödé", "third", "fourth"]
            with open(os.path.join(v1, "LOG.txt"), "wb") as f:
                for i, t in enumerate(texts):
                    b = ("#%d 2026-07-%02d %s" % (i, i + 1, t)).encode()
                    f.write(b + b" " * (320 - 1 - len(b)) + b"\n")
            with open(os.path.join(v1, "TREE", "2"), "wb") as f:
                for t in ["sum of 0-1", "sum of 2-3"]:
                    b = t.encode()
                    f.write(b + b" " * (288 - 1 - len(b)) + b"\n")
            tsv = os.path.join(v1, "scopes.tsv")
            with open(tsv, "w") as f:
                f.write("# id\tscope\n1\tproj\n#3\tinfra/backup\n")
            out = ok("import-v1", v1, tsv)
            self.assertIn("Imported 4 memories into 3 scopes; 2 v1 summaries", out)
            self.assertIn("pending naps:", out)
            m = self.model()
            self.assertEqual([m.leaves[i]["scope"] for i in range(4)],
                             ["core", "proj", "core", "infra/backup"])
            self.assertEqual(m.leaves[1]["text"], "second thing with ünïcödé")
            self.assertEqual(m.leaves[3]["ts"], "2026-07-04T00:00:00")
            with open(os.path.join(self.d, "cache", "v1_hints.txt"), encoding="utf-8") as f:
                hints = f.read()
            self.assertEqual(hints, "#0-1\tsum of 0-1\n#2-3\tsum of 2-3\n")
            self.assertIn("#1 2026-07-02 second", ok("wake", "proj"))
            code, out = run("import-v1", v1, tsv)
            self.assertEqual(code, 1)
            self.assertIn("empty memory", out)
        finally:
            shutil.rmtree(v1)


class TestRecall(Base):
    def test_recall_leaves_and_summaries(self):
        seed(self.d, 30)
        seed(self.d, 2, scope="p", start=30)
        m = self.model()
        s, h = m.pending()[0]
        ok("nap", h[:6], "an era of GLORIOUS facts")
        out = ok("recall", "glorious")
        self.assertIn("[core] #%s an era of GLORIOUS facts" % h[:6], out)
        self.assertIn("1 match.", out)
        out = ok("recall", "fact number 7$")
        self.assertIn("[core] #1 2026-01-01 memory 1: fact number 7", out)
        out = ok("recall", "memory 3", "--scope", "p")
        self.assertIn("[p] #30", out)
        self.assertNotIn("[core]", out)
        self.assertIn("No match.", ok("recall", "zzz"))
        code, _ = run("recall", "(")
        self.assertEqual(code, 1)

    def test_zoom_and_scopes(self):
        seed(self.d, 30)
        m = self.model()
        t = m.trees["core"]
        out = ok("zoom", t.root[:6])
        self.assertIn("#%s: scope core" % t.root[:6], out)
        n = t.nodes[t.root]
        self.assertEqual(len(out.splitlines()), 1 + len(n.children))
        self.assertIn("[core] #4 2026-01-01 memory 4", ok("zoom", "4"))
        out = ok("scopes")
        self.assertIn("core", out)
        self.assertIn("(no summary yet)", out)
        self.assertIn("core: 30 leaves", ok("check"))


class TestAmendments(Base):
    def test_root_nap_and_dormant_line(self):
        seed(self.d, 3)
        t = self.open_root_scope("z", 100)
        m = self.model()
        # root naps only on explicit request, after everything else in z
        self.assertNotIn(("z", t.root), m.pending())
        self.assertNotIn(("z", t.root), m.pending(roots=True)[:-1])
        n = 0
        while m.pending("z"):
            ok("nap", m.pending("z")[0][1][:8], "era %d" % n)
            n += 1
            m = self.model()
        self.assertEqual(m.pending("z"), [])
        self.assertEqual(m.pending("z", roots=True), [("z", t.root)])
        # meanwhile the dormant line shows the newest cached child summary
        newest = next(m.summaries[c] for c in reversed(t.nodes[t.root].children)
                      if c in m.summaries)
        self.assertIn("z (%d): (newest era) %s" % (m.count("z"), newest), ok("wake"))
        core_root = self.model().trees["core"].root
        ok("nap", core_root[:8], "core in one line")  # core sorts first
        out = ok("nap")
        self.assertIn("Compress the whole scope z (root #%s)" % t.root[:6], out)
        out = ok("nap", t.root[:8], "all of z in one line")
        self.assertIn("Scope z summarised", out)
        m = self.model()
        self.assertFalse(m.pending("z", roots=True))
        self.assertNotIn(t.root, m.summaries)  # scope summaries live in roots.jsonl
        self.assertEqual(m.roots["z"]["nleaves"], m.count("z"))
        self.assertIn("z (%d): all of z in one line\n" % m.count("z"), ok("wake"))
        self.assertIn("all of z in one line", ok("scopes"))
        # new notes: summary stays, marked (+N); no root nap is re-asked
        # after a write, and not by `nap` either until ROOT_STALE notes
        for k in range(memo.ROOT_STALE - 1):
            out = ok("note", "more z %d" % k, "--scope", "z")
            self.assertNotIn("whole scope z", out)
        m = self.model()
        self.assertIn("z (%d): all of z in one line (+%d)" % (m.count("z"), memo.ROOT_STALE - 1),
                      ok("wake"))
        # the new notes carry wall-clock timestamps, so whether they closed a
        # chunk varies run to run; only the ROOT must not be re-asked yet
        self.assertNotIn(("z", self.model().trees["z"].root), m.pending("z", roots=True))
        self.assertIn("orphan summaries: 0", ok("check"))
        ok("note", "the straw", "--scope", "z")
        m = self.model()
        while m.pending("z"):  # new chunks may have closed meanwhile
            ok("nap", m.pending("z")[0][1][:8], "late era")
            m = self.model()
        # the wall-clock notes may have made the root itself complete; then it
        # was napped as a normal node above and there is no root nap left
        root = m.trees["z"].root
        want = [] if m.trees["z"].nodes[root].complete else [("z", root)]
        self.assertEqual(m.pending("z", roots=True), want)
        self.assertIn("orphan summaries: 0", ok("check"))
        # --json follows the same rule
        import json
        every = json.loads(ok("nap", "--json", "--all"))
        self.assertEqual(root in [e["node"] for e in every], bool(want))
        # a scope whose root is a leaf has nothing to nap
        seed(self.d, 1, scope="one", start=900)
        self.assertFalse(self.model().pending("one", roots=True))

    def test_no_one_child_nodes(self):
        rnd = random.Random(11)
        recs = []
        for i in range(500):
            recs.append({"t": "note", "id": i, "ts": "2026-%02d-%02dT%02d:00:00"
                         % (1 + i // 600, 1 + (i // 24) % 28, i % 24),
                         "scope": "core", "text": "r%d %s" % (i, rnd.random())})
        memo.append_json(memo.log_path(self.d), recs)
        t = self.model().trees["core"]
        self.assertGreater(len(t.nodes), 50)
        for n in t.nodes.values():
            self.assertGreater(len(n.children), 1)
        self.assertEqual(t.leaf_set(t.root), set(range(500)))
        # flat: chunks average >= 2 items, so depth is about log_3(n) + 2
        depth = max(n.level for n in t.nodes.values())
        self.assertLessEqual(depth, math.log(500, 3) + 2)

    def test_all_closing_level_pairs(self):
        seed(self.d, 12)
        memo.FANOUT = 1  # every hash closes: chunks close at their 2nd item
        try:
            t = self.model().trees["core"]
        finally:
            memo.FANOUT = memo.KNOBS["FANOUT"][0]
        by_level = {}
        for n in t.nodes.values():
            by_level.setdefault(n.level, []).append(n)
        self.assertEqual([len(by_level[l]) for l in sorted(by_level)], [6, 3, 1, 1])
        self.assertTrue(all(len(n.children) == 2 for n in by_level[1]))
        self.assertEqual(t.leaf_set(t.root), set(range(12)))

    def test_wake_cap_without_summaries(self):
        seed(self.d, 300)
        ok("config", "WAKE_CHARS=4000")
        memo.config(self.d)
        out = ok("wake")
        self.assertLessEqual(len(out.encode()), 4000)
        self.assertRegex(out, re.compile(r"^\u2026 \d+ older memories omitted \(pending naps: \d+\)", re.M))
        self.assertIn("#299 2026-01-13 memory 299", out)
        self.assertIn("bytes: %d |" % len(out.encode()), out)
        # with two woken scopes the cap still holds and core is served first
        seed(self.d, 300, scope="p", start=300)
        ok("config", "CORE_CHARS=3000")
        memo.config(self.d)
        out = ok("wake", "p")
        self.assertLessEqual(len(out.encode()), 4000)
        core, p = out.split("== p (")
        self.assertGreater(len(core.encode()), len(p.encode()))
        self.assertIn("#599 2026-01-25 memory 599", out)


class TestNapJson(Base):
    def test_shape_and_hints(self):
        import json
        self.assertEqual(json.loads(ok("nap", "--json")), None)
        self.assertEqual(json.loads(ok("nap", "--json", "--all")), [])
        seed(self.d, 40)
        m = self.model()
        t = m.trees["core"]
        todo = m.pending()
        s, h = todo[0]
        n = t.nodes[h]
        ids = sorted(t.leaf_set(h))
        # hints: one v1 block inside this node's span, one far outside it
        with open(os.path.join(self.d, "cache", "v1_hints.txt"), "w") as f:
            f.write("#%d-%d\tinside hint\n#%d-%d\toutside hint\n#900-901\tunknown ids\n"
                    % (ids[0], ids[-1], ids[-1] + 30, ids[-1] + 35))
        one = json.loads(ok("nap", "--json"))
        self.assertEqual(one["node"], h)
        self.assertEqual(one["scope"], "core")
        self.assertEqual(one["level"], n.level)
        self.assertEqual(one["span"], {"from_ts": m.leaves[ids[0]]["ts"],
                                       "to_ts": m.leaves[ids[-1]]["ts"], "leaves": len(ids)})
        self.assertEqual([c["id"] for c in one["children"]], ids)
        self.assertTrue(all(c["kind"] == "leaf" and set(c) == {"kind", "id", "ts", "text"}
                            for c in one["children"]))
        self.assertEqual(one["hints"], ["#%d-%d inside hint" % (ids[0], ids[-1])])
        self.assertEqual(one["remaining"], len(todo) - 1)
        every = json.loads(ok("nap", "--json", "--all"))
        self.assertEqual([e["node"] for e in every], [x for _, x in todo])
        self.assertEqual([e["remaining"] for e in every], list(range(len(todo) - 1, -1, -1)))
        # a summary child once a level-1 node is settled
        ok("nap", h[:8], "settled era")
        parent = next(x for x in t.nodes.values() if h in x.children)
        entry = next((e for e in json.loads(ok("nap", "--json", "--all")) if e["node"] == parent.hash), None)
        if entry is not None:
            self.assertIn({"kind": "summary", "node": h, "text": "settled era"}, entry["children"])
        # prose output is unchanged by default
        self.assertIn("Compress ", ok("nap"))


class TestReviewFixes(Base):
    def test_f1_wake_is_fast_at_20k(self):
        import time
        recs = [{"t": "note", "id": i, "ts": "2026-%02d-%02dT%02d:%02d:00"
                 % (1 + (i // 43200) % 12, 1 + (i // 1440) % 28, (i // 60) % 24, i % 60),
                 "scope": "core", "text": "memory %d" % i} for i in range(20000)]
        memo.append_json(memo.log_path(self.d), recs)
        t0 = time.monotonic()
        out = ok("wake")
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertLessEqual(len(out.encode()), memo.WAKE_CHARS)
        self.assertIn("older memories omitted", out)

    def test_f2_sealed_remnant_never_swallows_a_note(self):
        seed(self.d, 5)
        rec = {"t": "note", "id": 5, "ts": "2026-02-01T00:00:00", "scope": "core",
               "text": "written just before the crash"}
        with open(memo.log_path(self.d), "ab") as f:
            f.write(json.dumps(rec).encode())  # cut exactly at the closing brace
        self.assertTrue(self.model().truncated)
        self.assertEqual(self.model().next_id, 6)
        ok("note", "written after the crash")
        m = self.model()
        self.assertFalse(m.truncated)
        self.assertEqual(m.leaves[5]["text"], "written just before the crash")
        self.assertEqual(m.leaves[6]["text"], "written after the crash")
        self.assertEqual(m.malformed, [])

    def test_f4_root_prompt_opens_grandchildren(self):
        t = self.open_root_scope("z", 100)
        m = self.model()
        # make every complete node settled so the root is offered
        while m.pending("z"):
            ok("nap", m.pending("z")[0][1][:8], "s")
            m = self.model()
        out = memo.nap_prompt(m, "z", t.root, 0)
        self.assertNotIn("(no summary yet)", out)
        for i in sorted(t.leaf_set(t.root)):
            if not any(i in t.leaf_set(c) for c in t.nodes[t.root].children
                       if c in m.summaries):
                self.assertIn("#%d " % i, out)

    def test_f5_many_scopes_fit(self):
        for k in range(120):
            seed(self.d, 3, scope="s%03d" % k, start=1000 + 3 * k)
        seed(self.d, 3)
        out = ok("wake", *["s%03d" % k for k in range(120)])
        self.assertLessEqual(len(out.encode()), memo.WAKE_CHARS)
        self.assertIn("== core (3 memories)", out)
        self.assertIn("== s000 (3 memories)", out)
        self.assertIn("-- other scopes:", out)
        self.assertIn("bytes: %d |" % len(out.encode()), out)
        ok("config", "WAKE_CHARS=1500")
        memo.config(self.d)
        out = ok("wake", *["s%03d" % k for k in range(120)])
        self.assertLessEqual(len(out.encode()), 1500)
        self.assertIn("== core (3 memories)", out)

    def test_f6_zoom_all_digit_node_prefix(self):
        seed(self.d, 30)
        m = self.model()
        t = m.trees["core"]
        n = next(iter(t.nodes.values()))
        # a fake all-digit prefix would hit the leaf branch; pick digits that
        # are NOT a live id (ids are 0..29) by using the full hash
        out = ok("zoom", n.hash)
        self.assertIn("#%s: scope core" % n.hash[:6], out)
        code, out = run("zoom", "9999999")
        self.assertEqual(code, 1)
        self.assertIn("No node", out)
        self.assertIn("[core] #4 2026-01-01 memory 4", ok("zoom", "4"))

    def test_f7_import_rejects_partial_and_bad_text(self):
        v1 = tempfile.mkdtemp(prefix="memov1.")
        try:
            os.makedirs(os.path.join(v1, "TREE"))
            with open(os.path.join(v1, "LOG.txt"), "wb") as f:
                b = b"#0 2026-07-01 fine"
                f.write(b + b" " * (320 - 1 - len(b)) + b"\n")
                b = b"#1 2026-07-02 " + b"x" * 300
                f.write(b + b" " * (320 - 1 - len(b)) + b"\n")
            tsv = os.path.join(v1, "t.tsv")
            open(tsv, "w").close()
            code, out = run("import-v1", v1, tsv)
            self.assertEqual(code, 1)
            self.assertIn("#1: Too long", out)
            self.assertFalse(self.model().leaves)
            with open(os.path.join(v1, "LOG.txt"), "ab") as f:
                f.write(b"#2 2026-07-03 partial")
            code, out = run("import-v1", v1, tsv)
            self.assertEqual(code, 1)
            self.assertIn("661 bytes, not a multiple of 320", out)
        finally:
            shutil.rmtree(v1)

    def test_note_ts_backdate(self):
        ok("note", "old", "--ts", "2025-03-04")
        ok("note", "older", "--ts", "2025-01-02T03:04:05")
        ok("note", "now")
        m = self.model()
        self.assertEqual(m.leaves[0]["ts"], "2025-03-04T12:00:00")
        self.assertEqual(m.leaves[1]["ts"], "2025-01-02T03:04:05")
        order = [int(l.split()[0][1:]) for l in ok("wake").splitlines()
                 if re.match(r"#\d+ 20", l)]
        self.assertEqual(order, [1, 0, 2])
        code, out = run("note", "bad", "--ts", "yesterday")
        self.assertEqual(code, 1)

    def test_nits_move_tag_and_broken_config(self):
        seed(self.d, 4)
        ok("drop", "2")
        out = ok("move", "1-2", "--to", "p", "--yes")
        self.assertIn("[core] (dropped) #2 ", out)
        with open(os.path.join(self.d, "config"), "a") as f:
            f.write("BOGUS = 5\nWAKE_CHARS = -1\n")
        code, out = run("wake")
        self.assertEqual(code, 1)
        code, out = run("config", "WAKE_CHARS=1234")
        self.assertEqual(code, 0)
        self.assertIn("WAKE_CHARS     1234", out)
        self.assertEqual(ok("wake").count("You are awake."), 1)  # file repaired


if __name__ == "__main__":
    unittest.main()
