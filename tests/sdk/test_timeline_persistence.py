"""Working-copy persistence across real processes: restart, crashes, partial writes, two writers, stale bases, discard.

Every "process" in this file is a child interpreter (``worker_main``) that keeps its own ``Checkout`` in memory
until it exits, so the only thing two writers share is the working-copy file on disk. Each worker:

- uses a temp data root (``BANODOCO_LOCAL_DATA_ROOT``) and a temp ``ASTRID_HOME``;
- opens the timeline through a fake client that reads a head file the test controls;
- refuses to open the live runtime (``AstridClient.open_from_launcher`` raises), and stubs the publish
  transport, so publish runs its real three-way guard and stops at the stubbed write.
"""
from __future__ import annotations

import copy
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

THIS_FILE = Path(__file__).resolve()
sys.path.insert(0, str(THIS_FILE.parent))  # test_timeline_checkout's fixtures, in the parent and in workers
REPO = THIS_FILE.parents[2]
TIMEOUT = 120  # seconds a worker may take to answer one request before the test calls it hung
CRASH_EXIT = 9

from test_timeline_checkout import bundle  # noqa: E402  (the same two-shot timeline the checkout tests use)
from astrid.sdk.timeline_checkout import Checkout, draft_path  # noqa: E402

CUT_A = "c1"  # the first cut, named as the sheet and show print it
BOOT = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('tp_persist', sys.argv[1])\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "mod.worker_main()\n"
)


class Refused(Exception):
    """The worker's Checkout refused the operation (the message is what the user would read)."""


# ---------------------------------------------------------------- the worker (runs in a child process)

class _FakeTimelines:
    """Reads the head from a file the test controls; the head is whatever the test last 'published'."""

    def __init__(self, head_file: str):
        self.head_file = Path(head_file)

    def _head(self) -> dict:
        return json.loads(self.head_file.read_text(encoding="utf-8"))

    def open_composition(self, project, timeline, **_kw):
        head = self._head()
        return SimpleNamespace(ok=True, error="", data={
            "native_inspection": {"project_id": "p", "timeline_id": "t"},
            "summary": {"head_revision_id": head["revision_id"]}})

    def open_bundle(self, project, timeline, revision_id=None):
        head = self._head()
        if revision_id and revision_id != head["revision_id"]:
            return SimpleNamespace(ok=False, error=f"no revision {revision_id}", data=None)
        return SimpleNamespace(ok=True, error="", data={"bundle": head["bundle"]})


def worker_main() -> None:
    """Serve JSON-line requests on stdin; answer on the real stdout. Library prints go to stderr."""
    proto = sys.stdout
    sys.stdout = sys.stderr
    from astrid.sdk import AstridClient, authoring_bundle, autobootstrap, workspace_client
    from astrid.sdk import timeline_checkout as tc

    def no_live_runtime(*_a, **_k):
        raise RuntimeError("persistence tests must never reach the live runtime")

    AstridClient.open_from_launcher = classmethod(no_live_runtime)
    tc.Checkout.SAY_SAVED = False
    publish_seen = {"candidate": None, "runtime_touched": False}

    def stub_ensure_runtime(**_kw):
        publish_seen["runtime_touched"] = True
        return {"endpoint": "http://127.0.0.1:9", "credential_file": "/nonexistent"}

    def stub_publish(candidate, _writer, *, idempotency_key):
        publish_seen["candidate"] = json.loads(json.dumps(candidate, default=str))
        return {"publication": {"new_head": "rev-published"}}

    class _StubTransport:
        def __init__(self, *_a):
            pass

    autobootstrap.ensure_runtime = stub_ensure_runtime
    authoring_bundle.publish_authoring_candidate = stub_publish
    workspace_client.resolve_runtime_connection = lambda endpoint, cred: (endpoint, "t")
    workspace_client.WorkspaceClient = _StubTransport

    client = SimpleNamespace(timelines=_FakeTimelines(os.environ["TP_HEAD"]))
    tl = {}

    def handle(op: str, req: dict):
        if op == "draft":
            tl["h"] = tc.Checkout.draft("p", "t", client=client)
            return {"path": str(tl["h"].path), "name": tl["h"].draft_name}
        if op == "find":
            found = tc.find_draft("p", "t", client=client)
            return str(found) if found else None
        h = tl["h"]
        if op == "state":
            return {
                "base": h.base_revision,
                "clips": {c.id: {"start": round(c.start, 3), "params": dict(c.params)} for c in h.clips()},
                "notes": h.cut_notes_all(),
                "merged": list(h.merged),
            }
        if op == "nudge":
            h.clip(req["clip"]).nudge(req["seconds"])
            return None
        if op == "set":
            h.clip(req["clip"]).set(**req["params"])
            return None
        if op == "note":
            h.set_cut_note(req["cut"], why=req["why"])
            return None
        if op == "save":
            return str(h.save(quiet=True, force=bool(req.get("force"))))
        if op == "save_dying":  # die inside the save: lock held, temp file written, target not replaced
            os.replace = lambda *_a, **_k: os._exit(CRASH_EXIT)
            h.save(quiet=True)
            return None
        if op == "undo":
            return h.undo(int(req.get("steps", 1)))
        if op == "discard":
            h.discard()
            return None
        if op == "publish":
            receipt = tc.publish_bundle(h.document(), "persistence-test", client=client, force=bool(req.get("force")))
            return {"merged": receipt.get("merged"), "new_head": receipt.get("new_head"),
                    "candidate": publish_seen["candidate"], "runtime_touched": publish_seen["runtime_touched"]}
        raise ValueError(f"unknown op {op!r}")

    for line in sys.stdin:
        req = json.loads(line)
        op = req.pop("op")
        try:
            reply = {"ok": True, "result": handle(op, req)}
        except Exception as exc:  # the parent reads this as a refusal or failure
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        proto.write(json.dumps(reply, default=str) + "\n")
        proto.flush()


# ---------------------------------------------------------------- the parent side

class Writer:
    """One child process holding one working-copy handle. Use as a context manager for a clean exit."""

    def __init__(self, world: SimpleNamespace, name: str):
        self.name = name
        self.rc: int | None = None
        self.err_path = world.tmp / f"{name}-{len(world.writers)}.err"
        self._err = open(self.err_path, "w", encoding="utf-8")
        env = {**os.environ, "BANODOCO_LOCAL_DATA_ROOT": str(world.root), "TP_HEAD": str(world.head),
               "ASTRID_HOME": str(world.tmp / "astrid-home"), "ASTRID_WORKSPACE_CONFIG_DIR": str(world.tmp / "ws")}
        self.proc = subprocess.Popen(
            [sys.executable, "-c", BOOT, str(THIS_FILE), "worker"], cwd=str(REPO), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, text=True, encoding="utf-8")
        world.writers.append(self)

    def send(self, op: str, **kw):
        """The worker's reply dict, or None if the process exited without answering."""
        try:
            self.proc.stdin.write(json.dumps({"op": op, **kw}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return None
        ready, _, _ = select.select([self.proc.stdout], [], [], TIMEOUT)
        if not ready:
            self.proc.kill()
            raise AssertionError(f"{self.name}: worker hung on {op!r}")
        line = self.proc.stdout.readline()
        return json.loads(line) if line else None

    def do(self, op: str, **kw):
        reply = self.send(op, **kw)
        if reply is None:
            raise AssertionError(f"{self.name}: worker exited during {op!r}: {self.stderr()}")
        if not reply["ok"]:
            raise Refused(reply["error"])
        return reply["result"]

    def close(self, expect_exit: int | None = 0) -> int:
        if self.rc is None:
            if self.proc.poll() is None:
                self.proc.stdin.close()
            self.rc = self.proc.wait(timeout=60)
            if expect_exit is not None and self.rc != expect_exit:
                raise AssertionError(f"{self.name} exited {self.rc}: {self.stderr()}")
            self._err.close()
        return self.rc

    def stderr(self) -> str:
        self._err.flush()
        return self.err_path.read_text(encoding="utf-8")[-2000:]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close(expect_exit=None if exc[0] else 0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(root))  # the parent computes draft paths from the same root
    head = tmp_path / "head.json"
    head.write_text(json.dumps({"revision_id": "rev-0", "bundle": pinned(timeline())}), encoding="utf-8")
    w = SimpleNamespace(tmp=tmp_path, root=root, head=head, writers=[])
    w.writer = lambda name: Writer(w, name)
    w.working_copy = lambda: draft_path("p", "t", "main")
    w.move_head = lambda rev, edit: _move_head(head, rev, edit)
    w.read_state = lambda: _read_state(w)
    yield w
    for writer in w.writers:  # never leave a child behind, even when an assertion failed
        if writer.proc.poll() is None:
            writer.proc.kill()
            writer.proc.wait(timeout=30)
        if not writer._err.closed:
            writer._err.close()


def timeline() -> dict:
    """The fixture timeline with its two cuts named as the sheet names them (c1, c2), on their picture clips."""
    doc = bundle()
    doc["shots"]["A"]["internal_timeline"]["clips"][0]["app"] = {"cut": "c1", "layer": "plate"}
    doc["shots"]["B"]["internal_timeline"]["clips"][0]["app"] = {"cut": "c2", "layer": "plate"}
    return doc


def pinned(bundle_doc: dict) -> dict:
    """A published head carries its pinned base, as the authoring bundle does (core/timeline/authoring_bundle.py).
    Without it a checkout cannot tell its own edits from the base, and publish would merge nothing."""
    doc = copy.deepcopy(bundle_doc)
    doc["schema_version"] = 1
    doc["base_parent_payload"] = copy.deepcopy(doc["parent"])
    doc["base_placements"] = {row["occurrence_id"]: copy.deepcopy(row) for row in doc["placements"]}
    doc["source_mapping"] = {"placements": copy.deepcopy(doc["base_placements"]), "shots": {}}
    for shot in doc["shots"].values():
        shot["base_payload"] = copy.deepcopy(shot["payload"])
        shot["base_internal_timeline"] = copy.deepcopy(shot["internal_timeline"])
    return doc


def _move_head(head: Path, rev: str, edit) -> None:
    """Someone else published: the head is now ``rev``, the fixture timeline with ``edit`` applied."""
    doc = timeline()
    edit(doc)
    head.write_text(json.dumps({"revision_id": rev, "bundle": pinned(doc)}), encoding="utf-8")


def _read_state(w) -> dict:
    with w.writer("reader") as r:
        r.do("draft")
        return r.do("state")


def _clip(bundle_doc: dict, cid: str) -> dict:
    for shot in bundle_doc["shots"].values():
        for clip in shot["internal_timeline"]["clips"]:
            if clip["id"] == cid:
                return clip
    raise KeyError(cid)


# ---------------------------------------------------------------- 1. restart

def test_restart_a_fresh_process_sees_every_edit_and_the_undo_history(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")
        a.do("set", clip="b-type", params={"text": "Now live."})
        a.do("save")
        a.do("note", cut=CUT_A, why="the rocket lands on Viral")
        a.do("save")

    with world.writer("B") as b:  # a fresh process: nothing from A is in memory
        opened = b.do("draft")
        state = b.do("state")
        assert Path(opened["path"]) == world.working_copy()
        assert state["base"] == "rev-0"
        assert state["clips"]["a-rocket"]["start"] == 1.5
        assert state["clips"]["b-type"]["params"]["text"] == "Now live."
        assert [note["why"] for note in state["notes"].values()] == ["the rocket lands on Viral"]

        assert len(b.do("undo")) == 1  # one edit taken back
        state = b.do("state")
        assert state["notes"] == {}  # undo took back the last edit: the cut note
        assert state["clips"]["b-type"]["params"]["text"] == "Now live."

        b.do("undo")  # the edit before that: the text goes back to A's previous value
        state = b.do("state")
        assert state["clips"]["b-type"]["params"]["text"] == "Live."
        assert state["clips"]["a-rocket"]["start"] == 1.5  # the move is an earlier edit and stays


# ---------------------------------------------------------------- 2. crash mid-save

def test_a_writer_that_dies_holding_the_lock_does_not_block_the_next_writer(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")

    lock = world.working_copy().with_name(f".{world.working_copy().name}.lock")
    with world.writer("C") as c:
        c.do("draft")
        c.do("set", clip="b-type", params={"text": "crashed edit"})
        assert c.send("save_dying") is None  # dies inside the save, holding the flock
        assert c.close(expect_exit=CRASH_EXIT) == CRASH_EXIT

    assert lock.exists()  # the lock file outlives its holder...
    assert len(list(world.working_copy().parent.glob(".main.json.*.tmp"))) == 1  # ...and so does the half-finished temp

    with world.writer("B") as b:  # the kernel released the flock with C: B saves, it does not hang or refuse
        b.do("draft")
        state = b.do("state")
        assert state["clips"]["b-type"]["params"]["text"] == "Live."  # the last good save, not C's unsaved edit
        b.do("set", clip="b-type", params={"text": "from B"})
        b.do("save")

    # a stale lock file naming a dead pid is not an obstacle either: the lock is a flock, not a pid file
    lock.write_text("999999\n", encoding="utf-8")
    with world.writer("D") as d:
        d.do("draft")
        d.do("set", clip="b-type", params={"text": "from D"})
        d.do("save")
    assert _read_state(world)["clips"]["b-type"]["params"]["text"] == "from D"


# ---------------------------------------------------------------- 3. partial write

def test_a_truncated_temp_file_is_never_read_as_the_working_copy(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")

    target = world.working_copy()
    good = target.read_text(encoding="utf-8")
    (target.parent / f".{target.name}.424242.tmp").write_text(good[: len(good) // 2], encoding="utf-8")  # cut off mid-write
    (target.parent / f".{target.name}.515151.tmp").write_text("", encoding="utf-8")  # or never written at all

    loaded = Checkout.load(target)  # the library reads the target, never a temp file
    assert loaded.clip("a-rocket").start == pytest.approx(1.5)
    assert target.read_text(encoding="utf-8") == good

    state = _read_state(world)
    assert state["clips"]["a-rocket"]["start"] == 1.5 and state["clips"]["a-rocket"]["params"]["x"] == 10


# ---------------------------------------------------------------- 4. two writers across processes

def test_two_writers_in_separate_processes_merge_different_clips_and_refuse_the_same_param(world):
    a = world.writer("A")
    b = world.writer("B")
    try:
        a.do("draft")
        b.do("draft")  # both load the same base
        a.do("nudge", clip="a-rocket", seconds=0.5)  # A edits clip X
        a.do("save")
        b.do("set", clip="b-type", params={"text": "Now live."})  # B edits clip Y
        b.do("save")
        assert any("merged with another writer's save" in m for m in b.do("state")["merged"])

        state = world.read_state()
        assert state["clips"]["a-rocket"]["start"] == 1.5  # A's move survived B's save
        assert state["clips"]["b-type"]["params"]["text"] == "Now live."  # and B's text survived A's

        a.do("draft")  # A reloads what B saved, then edits the SAME param B is about to edit, differently
        a.do("set", clip="b-type", params={"text": "from A"})
        a.do("save")
        b.do("set", clip="b-type", params={"text": "from B"})
        with pytest.raises(Refused, match="another writer.*both changed.*b-type"):
            b.do("save")
        assert world.read_state()["clips"]["b-type"]["params"]["text"] == "from A"  # A's edit is the one on disk
    finally:
        a.close(expect_exit=None)
        b.close(expect_exit=None)


# ---------------------------------------------------------------- 5. stale base after restart

def test_publish_after_a_restart_merges_other_clips_onto_the_moved_head(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")
    world.move_head("rev-1", lambda b: _clip(b, "b-type")["params"].__setitem__("text", "LIVE!"))

    with world.writer("B") as b:
        b.do("draft")
        assert b.do("state")["base"] == "rev-0"  # the working copy still remembers the base it started from
        published = b.do("publish")

    assert published["runtime_touched"] is True  # the write is the stubbed transport, not the runtime
    assert published["merged"] and "merged onto head rev-1" in published["merged"]
    merged = Checkout(published["candidate"])
    assert merged.clip("a-rocket").start == pytest.approx(1.5)  # our move
    assert merged.clip("b-type").params["text"] == "LIVE!"  # their change, kept


def test_publish_after_a_restart_refuses_a_clip_both_sides_changed(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")
    world.move_head("rev-1", lambda b: _clip(b, "a-rocket")["params"].__setitem__("x", 99))

    with world.writer("B") as b:
        b.do("draft")
        with pytest.raises(Refused, match="not published: someone published"):
            b.do("publish")
        assert b.do("state")["clips"]["a-rocket"]["start"] == 1.5  # the working copy is untouched by the refusal
    assert json.loads(world.head.read_text(encoding="utf-8"))["revision_id"] == "rev-1"  # nothing was written


# ---------------------------------------------------------------- 6. discard persists

def test_a_discard_in_one_process_leaves_the_next_process_with_no_working_copy(world):
    with world.writer("A") as a:
        a.do("draft")
        a.do("nudge", clip="a-rocket", seconds=0.5)
        a.do("save")
        a.do("discard")
    assert not world.working_copy().exists()
    assert not world.working_copy().with_suffix(".history").exists()

    with world.writer("B") as b:
        assert b.do("find") is None  # no working copy to see
        b.do("draft")  # a new one starts from the published head, not from A's discarded edit
        state = b.do("state")
    assert state["clips"]["a-rocket"]["start"] == 1.0
    assert state["clips"]["a-rocket"]["params"]["x"] == 10
