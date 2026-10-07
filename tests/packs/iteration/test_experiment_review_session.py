"""Tests for iteration.experiment_review_session orchestrator.

Covers session-artifact generation, the safe mounted-media mapping, HTTP Range
playback, traversal/symlink denial, schema-validated ``/submit``, and final
rubric validation.  The blocking review server is exercised directly through
``editorial.human_review``'s handler so tests stay offline and deterministic.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.contracts.errors import AstridError
from astrid.packs.iteration.actions.experiment_review_session import run as session_run

from astrid.core.experiments.state import (
    init_experiment_review_state,
    make_initial_experiment_review_state,
)
from astrid.packs.editorial.actions.human_review.run import (
    _parse_mounts,
    make_handler_class,
)
from astrid.packs.iteration.actions.experiment_review_session.run import (
    _resolve_media_mounts,
)
from astrid.packs.iteration.actions.experiment_review_session.run import (
    main as session_main,
)

RUN_ID = "00123456789ABCDEFGHJKMNPQR"


def _experiment_json(path: Path, *, run_id: str = RUN_ID) -> Path:
    exp = {
        "schema_version": 1,
        "experiment_id": "session-test",
        "project_slug": "test",
        "title": "Session Test",
        "question": "Does it play and submit?",
        "hypotheses": [],
        "factors": [{"id": "f", "values": ["a"]}],
        "rubric": [{"id": "quality", "label": "Quality", "scale": {"min": 1, "max": 5}}],
        "cases": [
            {
                "case_id": "case-a",
                "label": "Case A",
                "run_id": run_id,
                "factors": {"f": "a"},
                "relationship": {"type": "baseline", "case_id": None},
            }
        ],
        "created": "2026-07-27T00:00:00Z",
    }
    path.write_text(json.dumps(exp))
    return path


def _run_with_media(runs_dir: Path, run_id: str = RUN_ID) -> Path:
    rd = runs_dir / run_id
    rd.mkdir(parents=True)
    (rd / "out.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes for range test")
    (rd / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "kind": "generation.generate_image_fal",
                "modality": "image",
                "model": "flux-dev",
                "mode_used": "t2i",
                "execution": "cloud",
                "request": {"prompt": "a smoke image", "seed": 7, "count": 1},
                "outputs": [{"path": "out.png", "content_hash": "sha256:" + "a" * 64}],
                "seed": 7,
                "created": "2026-07-27T00:00:00Z",
                "warnings": [],
                "inputs": {},
            }
        )
    )
    return rd


@pytest.fixture()
def session_inputs(tmp_path):
    runs_dir = tmp_path / "runs"
    _run_with_media(runs_dir)
    exp_path = _experiment_json(tmp_path / "experiment.json")
    return exp_path, runs_dir


class TestSessionArtifacts:
    def test_skip_server_builds_all_artifacts(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        rc = session_main([
            "--experiment", str(exp_path),
            "--runs-dir", str(runs_dir),
            "--out", str(out),
            "--skip-server",
            "--no-open",
        ])
        assert rc == 0
        for name in ("data.json", "review_session.html", "response_schema.json", "media_map.json", "manifest.json"):
            assert (out / name).is_file(), name
        assert (out / "prepare" / "review.json").is_file()
        assert (out / "review.json").read_bytes() == (out / "prepare" / "review.json").read_bytes()

    def test_media_map_has_relative_prefix_only(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        mmap = json.loads((out / "media_map.json").read_text())
        assert mmap["media_mounts"][RUN_ID] == f"/media/{RUN_ID}"
        blob = (out / "media_map.json").read_text()
        assert str(runs_dir) not in blob  # no absolute paths persisted

    def test_response_schema_bounds_scores(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        schema = json.loads((out / "response_schema.json").read_text())
        scores = schema["properties"]["decisions"]["items"]["properties"]["scores"]["properties"]
        assert scores["quality"]["minimum"] == 1
        assert scores["quality"]["maximum"] == 5

    def test_session_html_is_self_contained(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        html = (out / "review_session.html").read_text()
        assert "<!DOCTYPE html>" in html
        # No external script/style dependencies.
        assert "https://" not in html
        assert "src=\"http" not in html


class TestResolveMediaMounts:
    def test_only_existing_runs_mounted(self, tmp_path):
        runs_dir = tmp_path / "runs"
        _run_with_media(runs_dir, RUN_ID)
        review = {"cases": [{"run_id": RUN_ID}, {"run_id": "0ZZZZZZZZZZZZZZZZZZZZZZZZZ"}]}
        server_mounts, persisted = _resolve_media_mounts(review, runs_dir)
        assert f"/media/{RUN_ID}" in server_mounts
        assert "0ZZZZZZZZZZZZZZZZZZZZZZZZZ" not in persisted

    def test_symlinked_run_root_outside_runs_dir_is_not_mounted(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        outside_run = tmp_path / "outside-run"
        outside_run.mkdir()
        (outside_run / "secret.png").write_bytes(b"outside media")
        (runs_dir / RUN_ID).symlink_to(outside_run, target_is_directory=True)

        server_mounts, persisted = _resolve_media_mounts(
            {"cases": [{"run_id": RUN_ID}]},
            runs_dir,
        )

        assert server_mounts == {}
        assert persisted == {}


class TestHumanReviewRoundTrip:
    """Exercises editorial.human_review directly with the session's schema and
    mounts: data read, media playback + Range, traversal/symlink denial, and
    schema-validated /submit."""

    @pytest.fixture()
    def server(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        rd = runs_dir / RUN_ID
        mounts = _parse_mounts([f"/media/{RUN_ID}={rd}"])
        shutdown = threading.Event()
        token = "tok123"
        Handler = make_handler_class(
            html_path=out / "review_session.html",
            data_path=out / "data.json",
            state_path=None,
            out_path=out / "review.final.json",
            schema_path=out / "response_schema.json",
            mounts=mounts,
            token=token,
            shutdown_event=shutdown,
        )
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield {
            "base": f"http://127.0.0.1:{port}",
            "token": token,
            "out": out,
            "run_dir": rd,
        }
        srv.shutdown()

    def _get(self, base, path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        return urllib.request.urlopen(req)

    def test_data_json_served(self, server):
        r = self._get(server["base"], "/data.json")
        assert r.status == 200
        data = json.loads(r.read())
        assert data["experiment_id"] == "session-test"

    def test_media_served_with_range(self, server):
        r = self._get(server["base"], f"/media/{RUN_ID}/out.png")
        assert r.status == 200
        body = r.read()
        assert len(body) > 0
        # HTTP Range playback (mp4 seek equivalent)
        req = urllib.request.Request(
            server["base"] + f"/media/{RUN_ID}/out.png", headers={"Range": "bytes=0-3"}
        )
        r = urllib.request.urlopen(req)
        assert r.status == 206
        assert r.headers.get("Content-Range") == f"bytes 0-3/{len(body)}"

    def test_traversal_denied(self, server):
        with pytest.raises(urllib.error.HTTPError) as ei:
            self._get(server["base"], f"/media/{RUN_ID}/../../../../etc/passwd")
        assert ei.value.code == 403

    def test_symlink_escape_denied(self, server):
        link = server["run_dir"] / "evil.png"
        try:
            os.symlink("/etc/passwd", link)
            with pytest.raises(urllib.error.HTTPError) as ei:
                self._get(server["base"], f"/media/{RUN_ID}/evil.png")
            assert ei.value.code == 403
        finally:
            link.unlink(missing_ok=True)

    def test_valid_submit_accepted(self, server):
        payload = {
            "schema_version": 1,
            "experiment_id": "session-test",
            "reviewer": {"type": "human", "id": "peter"},
            "decisions": [
                {
                    "case_id": "case-a",
                    "scores": {"quality": 4},
                    "verdict": "iterate",
                    "created": "2026-07-27T00:00:00Z",
                }
            ],
        }
        req = urllib.request.Request(
            server["base"] + "/submit?token=" + server["token"],
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": server["token"]},
            method="POST",
        )
        r = urllib.request.urlopen(req)
        assert r.status == 204
        assert (server["out"] / "review.final.json").is_file()

    def test_invalid_score_rejected(self, server):
        payload = {
            "schema_version": 1,
            "experiment_id": "session-test",
            "reviewer": {"type": "human", "id": "x"},
            "decisions": [
                {"case_id": "case-a", "scores": {"quality": 99}, "verdict": "x", "created": "t"}
            ],
        }
        req = urllib.request.Request(
            server["base"] + "/submit?token=" + server["token"],
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
        assert ei.value.code == 400


def _start_handler(*, out, state_path, token="tok123"):
    """Build and start a real editorial.human_review handler server."""
    mounts = _parse_mounts([])
    shutdown = threading.Event()
    Handler = make_handler_class(
        html_path=out / "review_session.html",
        data_path=out / "data.json",
        state_path=state_path,
        out_path=out / "review.final.json",
        schema_path=out / "response_schema.json",
        mounts=mounts,
        token=token,
        shutdown_event=shutdown,
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{port}", token


class TestSubmitSchemaEnforcement:
    """The server rejects bad /submit payloads before writing/finalizing."""

    @pytest.fixture()
    def server(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        srv, base, token = _start_handler(out=out, state_path=None)
        yield {"base": base, "token": token, "out": out}
        srv.shutdown()

    def _post(self, server, payload):
        req = urllib.request.Request(
            server["base"] + "/submit?token=" + server["token"],
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": server["token"]},
            method="POST",
        )
        try:
            r = urllib.request.urlopen(req)
            return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def _good(self):
        return {
            "schema_version": 1,
            "experiment_id": "session-test",
            "reviewer": {"type": "human", "id": "peter"},
            "decisions": [
                {"case_id": "case-a", "scores": {"quality": 4}, "verdict": "iterate",
                 "created": "2026-07-27T00:00:00Z"}
            ],
        }

    def test_wrong_experiment_id_rejected(self, server):
        bad = self._good()
        bad["experiment_id"] = "not-session-test"
        assert self._post(server, bad) == 400
        assert not (server["out"] / "review.final.json").exists()

    def test_unknown_case_rejected(self, server):
        bad = self._good()
        bad["decisions"][0]["case_id"] = "no-such-case"
        assert self._post(server, bad) == 400

    def test_duplicate_case_rejected(self, server):
        bad = self._good()
        bad["decisions"].append(dict(bad["decisions"][0]))  # two case-a
        assert self._post(server, bad) == 400

    def test_missing_case_rejected(self, server):
        bad = self._good()
        bad["decisions"] = []  # case-a missing
        assert self._post(server, bad) == 400


class TestExperimentReviewStateLifecycle:
    """Durable, versioned draft state through the real handler/server path:
    initialization, reload/persistence, version increment, stale-write 409."""

    @pytest.fixture()
    def session_out(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        return out

    def test_state_file_initialized_by_orchestrator(self, session_out):
        state_path = session_out / "review.state.json"
        assert state_path.is_file()
        st = json.loads(state_path.read_text())
        assert st["kind"] == "experiment_review_state"
        assert st["experiment_id"] == "session-test"
        assert st["state_version"] == 0
        assert st["draft"] == {}

    def test_state_served_and_persisted_across_handler_restart(self, session_out):
        state_path = session_out / "review.state.json"
        # First handler: save a draft.
        srv, base, token = _start_handler(out=session_out, state_path=state_path)
        try:
            req = urllib.request.Request(
                base + "/save?token=" + token,
                data=json.dumps({"experiment_id": "session-test", "base_state_version": 0, "draft": {"case-a.quality": "4"}}).encode(),
                headers={"Content-Type": "application/json", "X-Session-Token": token},
                method="POST",
            )
            assert urllib.request.urlopen(req).status == 200
        finally:
            srv.shutdown()
        # Fresh handler (browser-reload analogue) reads the same persisted file.
        srv2, base2, token2 = _start_handler(out=session_out, state_path=state_path)
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(base2 + "/state.json?token=" + token2,
                                       headers={"X-Session-Token": token2}))
            assert r.status == 200
            st = json.loads(r.read())
            assert st["state_version"] == 1
            assert st["draft"] == {"case-a.quality": "4"}
        finally:
            srv2.shutdown()

    def test_version_increments_on_each_save(self, session_out):
        state_path = session_out / "review.state.json"
        srv, base, token = _start_handler(out=session_out, state_path=state_path)
        try:
            v = 0
            for i, patch in enumerate([{"a": "1"}, {"a": "2", "b": "3"}]):
                req = urllib.request.Request(
                    base + "/save?token=" + token,
                    data=json.dumps({"experiment_id": "session-test", "base_state_version": v, "draft": patch}).encode(),
                    headers={"Content-Type": "application/json", "X-Session-Token": token},
                    method="POST",
                )
                resp = json.loads(urllib.request.urlopen(req).read())
                v += 1
                assert resp["state_version"] == v
            r = urllib.request.urlopen(
                urllib.request.Request(base + "/state.json?token=" + token,
                                       headers={"X-Session-Token": token}))
            st = json.loads(r.read())
            assert st["state_version"] == 2
            assert st["draft"]["b"] == "3"
        finally:
            srv.shutdown()

    def test_stale_base_version_rejected_with_409(self, session_out):
        state_path = session_out / "review.state.json"
        srv, base, token = _start_handler(out=session_out, state_path=state_path)
        try:
            # Advance to version 1.
            req = urllib.request.Request(
                base + "/save?token=" + token,
                data=json.dumps({"experiment_id": "session-test", "base_state_version": 0, "draft": {"x": "1"}}).encode(),
                headers={"Content-Type": "application/json", "X-Session-Token": token},
                method="POST",
            )
            urllib.request.urlopen(req).read()
            # Stale save still claiming base 0 → 409, version unchanged.
            stale = urllib.request.Request(
                base + "/save?token=" + token,
                data=json.dumps({"experiment_id": "session-test", "base_state_version": 0, "draft": {"x": "2"}}).encode(),
                headers={"Content-Type": "application/json", "X-Session-Token": token},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(stale)
            assert ei.value.code == 409
            body = json.loads(ei.value.read())
            assert body["error"] == "stale_state"
            r = urllib.request.urlopen(
                urllib.request.Request(base + "/state.json?token=" + token,
                                       headers={"X-Session-Token": token}))
            st = json.loads(r.read())
            assert st["state_version"] == 1
            assert st["draft"] == {"x": "1"}
        finally:
            srv.shutdown()


class TestExperimentReviewStateHelpers:
    """Unit-level coverage of the core state helpers (init idempotency, shape)."""

    def test_make_initial_shape(self):
        st = make_initial_experiment_review_state("exp-1", now="2026-07-27T00:00:00Z")
        assert st == {
            "schema_version": 1,
            "kind": "experiment_review_state",
            "experiment_id": "exp-1",
            "state_version": 0,
            "updated_at": "2026-07-27T00:00:00Z",
            "draft": {},
        }

    def test_init_is_idempotent_and_preserves_existing(self, tmp_path):
        path = tmp_path / "review.state.json"
        init_experiment_review_state(path, "exp-1")
        # Simulate a saved draft.
        st = json.loads(path.read_text())
        st["state_version"] = 5
        st["draft"] = {"case-a.quality": "4"}
        path.write_text(json.dumps(st))
        # Re-init must NOT clobber the in-flight draft.
        init_experiment_review_state(path, "exp-1")
        st2 = json.loads(path.read_text())
        assert st2["state_version"] == 5
        assert st2["draft"] == {"case-a.quality": "4"}

    def test_init_fails_closed_on_wrong_kind_file(self, tmp_path):
        # A wrong-kind file is preserved, never silently overwritten.
        path = tmp_path / "review.state.json"
        path.write_text(json.dumps({"kind": "something_else"}))
        before = path.read_text()
        with pytest.raises(Exception):
            init_experiment_review_state(path, "exp-1")
        assert path.read_text() == before


# ── Gate-G2 §2/§3: session identity gate + empty-case fail-early ───────────


def _experiment_with_no_included_cases(path: Path) -> Path:
    """An experiment whose only case is excluded — nothing to review."""
    exp = {
        "schema_version": 1,
        "experiment_id": "session-empty",
        "project_slug": "test",
        "title": "Empty",
        "question": "q?",
        "hypotheses": [],
        "factors": [{"id": "f", "values": ["a"]}],
        "rubric": [{"id": "quality", "label": "Q", "scale": {"min": 1, "max": 5}}],
        "cases": [
            {
                "case_id": "case-x",
                "label": "X",
                "run_id": RUN_ID,
                "factors": {"f": "a"},
                "relationship": {"type": "baseline", "case_id": None},
                "included": False,
            }
        ],
        "created": "2026-07-27T00:00:00Z",
    }
    path.write_text(json.dumps(exp))
    return path


class TestSessionEmptyCaseSetFailsEarly:
    def test_no_included_cases_raises(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        exp_path = _experiment_with_no_included_cases(tmp_path / "experiment.json")
        out = tmp_path / "session"
        rc = session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        # Fails early with an actionable error (nonzero exit).
        assert rc != 0


class TestSessionConclusionsIdentityGate:
    def test_wrong_experiment_conclusions_rejected(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        concl = tmp_path / "concl.json"
        concl.write_text(json.dumps({
            "schema_version": 1,
            "experiment_id": "not-session-test",
            "observations": [
                {"id": "obs-1", "type": "observation", "claim": "x", "evidence": []}
            ],
            "inferences": [],
            "decisions": [],
        }))
        out = tmp_path / "session"
        rc = session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
            "--conclusions", str(concl),
        ])
        assert rc != 0

    def test_matching_conclusions_embedded(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        concl = tmp_path / "concl.json"
        concl.write_text(json.dumps({
            "schema_version": 1,
            "experiment_id": "session-test",
            "observations": [
                {"id": "obs-1", "type": "observation", "claim": "seen", "evidence": []}
            ],
            "inferences": [],
            "decisions": [],
        }))
        out = tmp_path / "session"
        rc = session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
            "--conclusions", str(concl),
        ])
        assert rc == 0
        data = json.loads((out / "data.json").read_text())
        assert data["conclusions"]["experiment_id"] == "session-test"


class TestServerSaveIdentityGate:
    """Gate-G2 §1: the /save HTTP path rejects bad saves and leaves state unchanged."""

    @pytest.fixture()
    def server_with_state(self, session_inputs, tmp_path):
        exp_path, runs_dir = session_inputs
        out = tmp_path / "session"
        session_main([
            "--experiment", str(exp_path), "--runs-dir", str(runs_dir),
            "--out", str(out), "--skip-server", "--no-open",
        ])
        state_path = out / "review.state.json"
        srv, base, token = _start_handler(out=out, state_path=state_path)
        yield {"base": base, "token": token, "out": out, "state": state_path}
        srv.shutdown()

    def _post(self, server, payload):
        req = urllib.request.Request(
            server["base"] + "/save?token=" + server["token"],
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": server["token"]},
            method="POST",
        )
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as exc:
            return exc.code

    def _state_version(self, server):
        return json.loads(server["state"].read_text())["state_version"]

    def test_missing_experiment_id_rejected(self, server_with_state):
        code = self._post(server_with_state, {"base_state_version": 0, "draft": {"a": "1"}})
        assert code == 400
        assert self._state_version(server_with_state) == 0

    def test_wrong_experiment_id_rejected(self, server_with_state):
        before = server_with_state["state"].read_text()
        code = self._post(server_with_state, {
            "experiment_id": "not-session-test", "base_state_version": 0, "draft": {"a": "1"},
        })
        assert code == 400
        assert self._state_version(server_with_state) == 0
        assert server_with_state["state"].read_text() == before

    def test_bool_base_version_rejected(self, server_with_state):
        code = self._post(server_with_state, {
            "experiment_id": "session-test", "base_state_version": True, "draft": {"a": "1"},
        })
        assert code == 400
        assert self._state_version(server_with_state) == 0

    def test_string_base_version_rejected(self, server_with_state):
        code = self._post(server_with_state, {
            "experiment_id": "session-test", "base_state_version": "0", "draft": {"a": "1"},
        })
        assert code == 400
        assert self._state_version(server_with_state) == 0

    def test_valid_save_succeeds(self, server_with_state):
        code = self._post(server_with_state, {
            "experiment_id": "session-test", "base_state_version": 0, "draft": {"a": "1"},
        })
        assert code == 200
        assert self._state_version(server_with_state) == 1


# M09 bounded public-composition proof. The coordinator selects these tests;
# real Runtime tests reuse the accepted offline transport/normal vendor fixture.
_EXPECTED_SESSION_OUTPUTS = {
    "review": "review.json", "data": "data.json", "session_html": "review_session.html",
    "response_schema": "response_schema.json", "media_map": "media_map.json",
    "state": "review.state.json", "validated_final": "review.final.validated.json",
}


def _session_definition():
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.core.pack.discovery import DiscoveredPack
    from astrid.core.pack.loader import load_pack_manifest

    root = Path(__file__).resolve().parents[3] / "astrid/packs/iteration"
    pack = load_pack_manifest(root / "pack.yaml")
    return action_executor_definition(DiscoveredPack(pack, "source", 0),
                                      "experiment_review_session", pack.actions["experiment_review_session"])


def _child_inputs(root):
    root.mkdir(exist_ok=True)
    names = ("review_session.html", "data.json", "response_schema.json", "review.state.json")
    for name in names:
        (root / name).write_text("{}" if name.endswith("json") else "<html>review</html>")
    return dict(html_path=root / names[0], data_path=root / names[1], schema_path=root / names[2],
                state_path=root / names[3], out_path=root / "review.final.json", mounts={},
                port=43210, timeout=0, no_open=True)


def _review_child(root, *, ok=True, code=None, state="completed", task_id="review-task"):
    payloads = {"decisions": b'{"decisions":[]}', "state_result": b'{"state_version":2}'}
    rows = [{"association_id": "association-" + port, "output_port": port,
             "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
             "ordinal": index} for index, (port, data) in enumerate(payloads.items())]
    materialized = []

    def materialize(association_id):
        row = next(row for row in rows if row["association_id"] == association_id)
        destination = root / "child-outputs" / (row["output_port"] + ".json")
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(payloads[row["output_port"]])
        materialized.append(association_id)
        return SimpleNamespace(output=dict(row), filename=destination.relative_to(root).as_posix())

    return SimpleNamespace(ok=ok, error={"code": code} if code else None,
        raw_result={"state": state}, kernel_run_id="review-run", kernel_task_id=task_id,
        kernel_attempt_id="review-attempt", outputs={"managed_outputs": rows if ok else []},
        materialize_output=materialize, materialized=materialized)


class TestPublicHumanReviewComposition:
    def test_unlimited_wait_reuses_exact_child_and_producer_descriptors(self, tmp_path, monkeypatch):
        args = _child_inputs(tmp_path / "session")
        mount = tmp_path / "runs"; mount.mkdir()
        args["mounts"] = {"/media/one": mount, "/media/two": mount}
        initial = args["state_path"].read_bytes()
        results = [_review_child(args["out_path"].parent, ok=False, code="task_wait_timeout", state="running")
                   for _ in range(2)]
        completed = _review_child(args["out_path"].parent)
        results.append(completed)
        calls = []

        def invoke(capability, **kwargs):
            calls.append((capability, copy.deepcopy(kwargs)))
            assert args["state_path"].read_bytes() == initial
            return results.pop(0)

        monkeypatch.setattr(session_run.sdk, "invoke", invoke)
        session_run._run_human_review(**args)
        assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
        capability, kwargs = calls[0]
        assert capability == "editorial.human_review" and kwargs["kind"] == "action"
        assert kwargs["wait"] is True and 0 < kwargs["timeout_seconds"] < float("inf")
        assert kwargs["child_key"] == session_run._REVIEW_CHILD_KEY
        assert kwargs["inputs"]["serve"] == [f"/media/one={mount}", f"/media/two={mount}"]
        assert {name: kwargs["inputs"][name]["filename"] for name in
                ("html", "data", "response_schema", "state")} == {
                    "html": "review_session.html", "data": "data.json",
                    "response_schema": "response_schema.json", "state": "review.state.json"}
        assert kwargs["inputs"]["timeout"] == 0 and kwargs["inputs"]["port"] == 43210
        assert kwargs["inputs"]["no_open"] is True and "assets_bundle" not in kwargs["inputs"]
        assert args["out_path"].read_bytes() == b'{"decisions":[]}'
        assert args["state_path"].read_bytes() == b'{"state_version":2}'
        assert len(completed.materialized) == 2
        receipt = json.loads((args["out_path"].parent / "human_review.child.json").read_text())
        assert receipt["waits"] == 3 and receipt["task_id"] == "review-task" and receipt["state"] == "completed"

    @pytest.mark.parametrize("code,state", [("task_cancel_requested", "cancel_requested"),
        ("task_cancelled", "cancelled"), ("task_failed", "failed"),
        ("task_status_unavailable", "unknown"), ("child_authority_invalid", "unknown")])
    def test_non_wait_failure_propagates_without_retry_or_materialization(self, tmp_path, monkeypatch, code, state):
        args = _child_inputs(tmp_path / "session")
        initial = args["state_path"].read_bytes()
        result = _review_child(args["out_path"].parent, ok=False, code=code, state=state)
        calls = []
        monkeypatch.setattr(session_run.sdk, "invoke", lambda *a, **kw: calls.append(kw) or result)
        with pytest.raises(AstridError, match="did not complete"):
            session_run._run_human_review(**args)
        assert len(calls) == 1 and result.materialized == []
        assert args["state_path"].read_bytes() == initial and not args["out_path"].exists()
        assert json.loads((args["out_path"].parent / "human_review.child.json").read_text())["error"]["code"] == code

    def test_positive_timeout_is_passed_through_and_wait_timeout_is_terminal(self, tmp_path, monkeypatch):
        args = _child_inputs(tmp_path / "session"); args["timeout"] = 7; args["no_open"] = False
        calls = []
        result = _review_child(args["out_path"].parent, ok=False, code="task_wait_timeout", state="running")
        monkeypatch.setattr(session_run.sdk, "invoke", lambda *a, **kw: calls.append(kw) or result)
        with pytest.raises(AstridError): session_run._run_human_review(**args)
        assert len(calls) == 1 and calls[0]["inputs"]["timeout"] == 7
        assert calls[0]["inputs"]["no_open"] is False and calls[0]["timeout_seconds"] == 67.0

    def test_identity_change_between_waits_fails_closed(self, tmp_path, monkeypatch):
        args = _child_inputs(tmp_path / "session")
        results = [_review_child(args["out_path"].parent, ok=False, code="task_wait_timeout", state="running"),
                   _review_child(args["out_path"].parent, task_id="foreign-child")]
        monkeypatch.setattr(session_run.sdk, "invoke", lambda *a, **kw: results.pop(0))
        with pytest.raises(AstridError, match="changed the admitted child identity"):
            session_run._run_human_review(**args)
        assert not args["out_path"].exists()

    @pytest.mark.parametrize("tamper", ["missing", "duplicate", "bytes", "identity", "escape"])
    def test_final_materialization_requires_exact_settled_output_custody(self, tmp_path, monkeypatch, tamper):
        args = _child_inputs(tmp_path / "session")
        result = _review_child(args["out_path"].parent)
        rows = result.outputs["managed_outputs"]
        if tamper == "missing": rows.pop()
        elif tamper == "duplicate": rows.append(dict(rows[0]))
        elif tamper == "bytes": rows[0]["digest"] = "sha256:" + "0" * 64
        else:
            original = result.materialize_output
            def materialize(association_id):
                local = original(association_id)
                if tamper == "identity": local.output["association_id"] = "foreign"
                else:
                    outside = tmp_path / "outside.json"; outside.write_text("secret")
                    local.filename = str(outside)
                return local
            result.materialize_output = materialize
        monkeypatch.setattr(session_run.sdk, "invoke", lambda *a, **kw: result)
        with pytest.raises(AstridError): session_run._run_human_review(**args)
        assert not args["out_path"].exists() and args["state_path"].read_text() == "{}"

    def test_producer_cannot_escape_output_root(self, tmp_path, monkeypatch):
        args = _child_inputs(tmp_path / "session")
        outside = tmp_path / "outside.html"; outside.write_text("secret")
        args["html_path"] = outside
        monkeypatch.setattr(session_run.sdk, "invoke", lambda *a, **kw: pytest.fail("child must not start"))
        with pytest.raises(AstridError, match="escapes"): session_run._run_human_review(**args)


class TestSessionNamedReceipts:
    def test_declared_names_and_paths_match_actual_output_contract(self):
        definition = _session_definition()
        assert {port.name: port.path_template for port in definition.outputs} == {
            name: "{out}/" + path for name, path in _EXPECTED_SESSION_OUTPUTS.items()}
        assert all(port.type == "file" and port.mode == "create_or_replace" for port in definition.outputs)
        assert definition.metadata["output_result_manifest"] is True

    @pytest.mark.parametrize("finalized", [False, True])
    def test_named_harvest_has_six_or_seven_receipts(self, session_inputs, tmp_path, finalized):
        from astrid.core._shared.result_manifest import harvest_staged_outputs

        experiment, runs = session_inputs
        out = tmp_path / "session"
        assert session_main(["--experiment", str(experiment), "--runs-dir", str(runs),
            "--out", str(out), "--skip-server"]) == 0
        # A prior final file must not become an output of skip_server.
        (out / "review.final.validated.json").write_text("{}")
        session_run._write_orchestrator_manifest(out, json.loads(experiment.read_text()), finalized=finalized)
        rows = harvest_staged_outputs(out, definition=_session_definition())
        expected = set(_EXPECTED_SESSION_OUTPUTS) - (set() if finalized else {"validated_final"})
        assert {row["name"] for row in rows} == expected
        assert len(rows) == (7 if finalized else 6)
        assert all(Path(row["path"]).parent == out for row in rows)
        assert (out / "prepare/review.json").read_bytes() == (out / "review.json").read_bytes()

    def test_normal_unfiltered_public_discovery_finds_human_review(self):
        from astrid import sdk

        capability = sdk.get_capability("editorial.human_review", kind="action")
        assert capability.id == "editorial.human_review"
        assert {port.name for port in capability.inputs} >= {"html", "data", "serve", "state", "response_schema"}


@pytest.fixture
def m09_runtime_world(tmp_path, monkeypatch):
    # Reuse the accepted, audited offline Runtime and normal vendored transport.
    # This imports no central tests into this module's collection.
    from tests.core.execution.test_generic_host_child_bridge_d18 import world

    yield from world.__wrapped__(tmp_path, monkeypatch)


def test_public_review_session_machine_bound_large_direct_media_and_settlement(m09_runtime_world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from urllib.parse import urlsplit

    from astrid.core.execution.generic_host import GenericPackHost
    from tests.core.execution.test_generic_host_child_bridge_d18 import (
        _claim_snapshot_task, _real_receiver_url, _real_review_http, _snapshot_task, digest, resource,
    )

    world = m09_runtime_world
    candidate = Path(__file__).resolve().parents[3]
    roots = [candidate / "astrid/packs/iteration", candidate / "astrid/packs/editorial"]
    parent_id, child_id = "iteration.experiment_review_session", "editorial.human_review"
    runs = world.tmp / "runs"
    second_id = "00123456789ABCDEFGHJKMNPQS"
    _run_with_media(runs); _run_with_media(runs, second_id)
    media = runs / RUN_ID / "large.mp4"
    with media.open("wb") as stream:
        stream.write(b"large-media")
        stream.truncate(65 * 1024 * 1024)
    secret = runs / "unselected"; secret.mkdir(); (secret / "secret.txt").write_text("not selected")
    (runs / RUN_ID / "escape.txt").symlink_to(secret / "secret.txt")
    experiment_path = _experiment_json(world.tmp / "experiment.json")
    experiment = json.loads(experiment_path.read_text())
    experiment["cases"].append({**experiment["cases"][0], "case_id": "case-b", "run_id": second_id})
    experiment_path.write_text(json.dumps(experiment))
    raw_experiment = experiment_path.read_bytes()
    world.service.ingest(world.project, raw_experiment, media_type="application/json",
                         original_name="experiment.json", idempotency_key="m09-experiment")
    experiment_descriptor = {"object_id": digest(raw_experiment), "digest": digest(raw_experiment),
                             "filename": "experiment.json"}
    parent_host = GenericPackHost(pack_roots=roots, client=world.client, executor_id="worker",
                                 max_concurrency=2, attempt_root=world.tmp / "m09-parent")
    child_host = GenericPackHost(pack_roots=roots, client=world.client, executor_id="worker",
                                max_concurrency=2, attempt_root=world.tmp / "m09-child")
    parent_host.discover(); child_host.discover()
    for cid in (parent_id, child_id):
        record = parent_host.capabilities[cid]
        world.service.register_capability({"capability_id": cid, "definition_digest": record.capability_digest})
    world.service.register_executor({"executor_id": "worker", "capabilities": [parent_id, child_id],
                                    "max_concurrency": 2}, idempotency_key="m09-register")
    cap = {"capability_id": child_id, "capability_digest": parent_host.capabilities[child_id].capability_digest}
    policy = {"capabilities": [cap], "targets": [{"kind": "default"}], "input_object_ids": [],
              "recoverable_outputs": [{**cap, "output_ports": ["state_result"]}],
              "limits": {"max_children": 1}}
    machine = world.identity["execution_binding"]["actual"]
    world.service.create_task({"project": world.project, "capability_id": parent_id,
        "capability_digest": parent_host.capabilities[parent_id].capability_digest,
        "input_object_ids": [digest(raw_experiment)],
        "spec": {"inputs": {"experiment": experiment_descriptor, "runs_dir": str(runs),
                            "timeout": 0, "no_open": True}},
        "child_delegation": policy, "execution_request": {"schema_version": 1, "target": machine,
            "inputs": [{"name": "experiment", **experiment_descriptor, "required": True}]},
        "idempotency_key": "m09-parent"}, enforce_readiness=True)
    parent_claim = _claim_snapshot_task(world, parent_id, key="m09-claim-parent")
    parent_task = _snapshot_task(world, parent_claim)
    parent_task["execution_binding"] = parent_claim["execution_binding"]
    launched = threading.Event(); processes = []; child_claims = []
    original_track = child_host._track_process

    def track(process):
        original_track(process); processes.append(process); launched.set()

    monkeypatch.setattr(child_host, "_track_process", track)

    def serve_child():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            claim = None
            with world.lock:
                queued = world.service.store.conn.execute(
                    "SELECT id FROM tasks WHERE capability=? AND status='queued'", (child_id,)).fetchone()
                if queued:
                    # Verified foreign placement must not start this mounted child.
                    foreign = copy.deepcopy(world.identity)
                    foreign["execution_binding"]["actual"]["id"] = "other-machine"
                    wrong = world.service.claim_next({"executor_id": "worker", "capability_ids": [child_id],
                        "runtime_epoch": 1, "target": machine}, idempotency_key="m09-foreign-claim", identity=foreign)
                    assert set(wrong) == {"task", "waiting_reason"}
                    assert wrong["waiting_reason"] == "execution_binding_mismatch"
                    assert wrong["task"]["task_id"] == queued["id"]
                    assert wrong["task"]["state"] == "queued"
                    assert wrong["task"]["attempt_id"] is None
                    assert wrong["task"].get("lease_id") is None
                    unclaimed = world.service.task(queued["id"])["task"]
                    assert unclaimed["status"] == "queued"
                    assert unclaimed["attempt_id"] is None and unclaimed["lease_token"] is None
                    assert world.service.store.conn.execute(
                        "SELECT COUNT(*) FROM attempts WHERE task_id=?", (queued["id"],)).fetchone()[0] == 0
                    claim = _claim_snapshot_task(world, child_id, key="m09-claim-child")
                    assert claim["task_id"] == queued["id"]
                    child_claims.append(claim)
                    task = _snapshot_task(world, claim)
                    task["execution_binding"] = claim["execution_binding"]
            if claim is not None:
                return child_host.run_task({"task": task}, lease_token=claim["lease_id"],
                    attempt_id=claim["attempt_id"], fence=claim["fence"])
            time.sleep(0.01)
        raise AssertionError("public product caller did not admit Human Review")

    with ThreadPoolExecutor(max_workers=2) as pool:
        child_future = pool.submit(serve_child)
        parent_future = pool.submit(parent_host.run_task, {"task": parent_task},
            lease_token=parent_claim["lease_id"], attempt_id=parent_claim["attempt_id"], fence=parent_claim["fence"])
        try:
            url = _real_receiver_url(launched, processes, child_future)
            base = "http://" + urlsplit(url).netloc
            request = urllib.request.Request(base + f"/media/{RUN_ID}/large.mp4", headers={"Range": "bytes=0-3"})
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 206 and response.read() == b"larg"
                assert response.headers["Content-Range"] == f"bytes 0-3/{media.stat().st_size}"
            assert _real_review_http(url, f"/media/{second_id}/out.png")[0] == 200
            assert _real_review_http(url, "/media/unselected/secret.txt")[0] == 404
            assert _real_review_http(url, f"/media/{RUN_ID}/escape.txt")[0] == 403
            status, raw = _real_review_http(url, "/save", {
                "experiment_id": "session-test", "base_state_version": 0,
                "draft": {"case-a.notes": "acknowledged"}})
            assert status == 200 and json.loads(raw)["state_version"] == 1
            status, saved = _real_review_http(url, "/state.json")
            assert status == 200 and json.loads(saved)["draft"]["case-a.notes"] == "acknowledged"
            assert _real_review_http(url, "/state.json") == (status, saved)  # browser reload
            final = {"schema_version": 1, "experiment_id": "session-test",
                "reviewer": {"type": "human", "id": "reviewer"}, "decisions": [
                    {"case_id": cid, "scores": {"quality": 4}, "verdict": "iterate",
                     "created": "2026-07-27T00:00:00Z"} for cid in ("case-a", "case-b")]}
            assert _real_review_http(url, "/submit", final) == (204, b"")
            assert child_future.result(timeout=15)
            assert parent_future.result(timeout=15)
        finally:
            if not parent_future.done() or not child_future.done():
                with world.lock:
                    world.service.cancel_task_canonical(parent_claim["task_id"], {}, idempotency_key="m09-cleanup")
                parent_host.shutdown(); child_host.shutdown()
            else:
                parent_host.shutdown(); child_host.shutdown()

    assert len(child_claims) == 1
    delegated = [body for _, path, body in world.calls if path == "/v1/delegated-tasks"]
    assert len(delegated) == 1
    values = delegated[0]["task"]["spec"]["inputs"]
    assert values["serve"] == [f"/media/{RUN_ID}={runs / RUN_ID}", f"/media/{second_id}={runs / second_id}"]
    assert "assets_bundle" not in values
    from astrid.core.execution._child_bridge import task_resource

    parent = task_resource(world.client.task(parent_claim["task_id"]))
    child = task_resource(world.client.task(child_claims[0]["task_id"]))
    assert parent["state"] == child["state"] == "succeeded"
    assert parent["execution_binding"]["status"] == child["execution_binding"]["status"] == "released"
    assert parent["execution_binding"]["actual_target"] == child["execution_binding"]["actual_target"] == machine
    rows, cursor = world.client.generated.list_managed_outputs(parent_claim["task_id"])
    rows = [resource(row) for row in rows]
    parent_results = [row for row in rows if row["task_id"] == parent_claim["task_id"]
                      and row["attempt_id"] == parent_claim["attempt_id"] and row["role"] == "result"]
    assert cursor is None and len(parent_results) == len(_EXPECTED_SESSION_OUTPUTS)
    assert {row["output_port"] for row in parent_results} == set(_EXPECTED_SESSION_OUTPUTS)
    for row in rows:
        data = world.client.get_object(row["digest"][7:])
        assert len(data) == row["size"] and digest(data) == row["digest"]
    state_rows = [row for row in parent_results if row["output_port"] == "state"]
    assert len(state_rows) == 1
    assert world.client.get_object(state_rows[0]["digest"][7:]) == saved
    assert all(path.stat().st_size < 64 * 1024 * 1024
               for spool in (parent_host.attempt_root, child_host.attempt_root)
               for path in spool.rglob("*") if path.is_file())
    assert media.stat().st_size == 65 * 1024 * 1024
