"""``timelines log``: the listing, --json, --actor filter, --verify (intact and broken), and no journal."""
from __future__ import annotations

import json

import pytest

from astrid.packs.timeline import cli
from astrid.sdk.timeline import journal
from astrid.sdk.timeline import store as _store


@pytest.fixture
def journal_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "_IN_VERB", False)
    monkeypatch.setattr(_store, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    return journal.journal_path("p", "t")


def _run(*argv) -> int:
    parsed = cli.build_parser(None).parse_args(list(argv))
    return parsed.handler(parsed)


def _add(path, label, *, actor="human:h", outcome="changed", cuts=()):
    kind, _, ident = actor.partition(":")
    journal.append(path, project="p", timeline="t", draft="main", actor={"kind": kind, "id": ident, "session": None},
                   source={"via": "cli", "detail": "timelines edit t"}, kind="save", outcome=outcome, label=label,
                   cuts=tuple(cuts))


def test_the_listing_is_one_line_per_record_newest_last(journal_root, capsys):
    _add(journal_root, "first edit", cuts=("c06",))
    _add(journal_root, "second edit", actor="agent:haiku-c4b", outcome="no-change")
    assert _run("log", "t", "--project", "P") == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].split()[0] == "1" and "first edit" in lines[0] and "(c06)" in lines[0]
    assert lines[1].split()[0] == "2" and "agent:haiku-c4b" in lines[1] and "save/no-change" in lines[1]


def test_json_prints_the_records_in_an_ok_envelope(journal_root, capsys):
    _add(journal_root, "only")
    assert _run("log", "t", "--project", "P", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["error"] is None
    (record,) = data["data"]["records"]
    assert record["seq"] == 1 and record["label"] == "only" and record["hash"].startswith("sha256:")


def test_filters_narrow_the_listing(journal_root, capsys):
    _add(journal_root, "a", actor="human:h")
    _add(journal_root, "b", actor="agent:t1", outcome="no-change")
    assert _run("log", "t", "--project", "P", "--actor", "agent:t1") == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "b" in out[0]


def test_verify_says_the_chain_is_intact_and_exits_zero(journal_root, capsys):
    _add(journal_root, "a")
    _add(journal_root, "b")
    assert _run("log", "t", "--project", "P", "--verify") == 0
    assert "chain intact, 2 record(s)" in capsys.readouterr().out


def test_verify_on_a_broken_chain_exits_one_and_names_the_problem(journal_root, capsys):
    _add(journal_root, "a")
    _add(journal_root, "b")
    _add(journal_root, "c")
    lines = journal_root.read_text(encoding="utf-8").splitlines()
    journal_root.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert _run("log", "t", "--project", "P", "--verify") == 1
    out = capsys.readouterr().out
    assert "BROKEN" in out and "expected" in out


def test_no_journal_yet_says_so(journal_root, capsys):
    # Main's listing reports an absent journal as "no records match"; --verify
    # keeps the "no journal yet" wording.
    assert _run("log", "t", "--project", "P") == 0
    assert "no journal records match for t" in capsys.readouterr().out
    assert _run("log", "t", "--project", "P", "--verify") == 0
    assert "no journal yet for t" in capsys.readouterr().out
