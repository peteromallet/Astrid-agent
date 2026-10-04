"""CPU-only ownership protocol proof; no kernel signaling qualification."""

from concurrent.futures import ThreadPoolExecutor
import signal
import threading

import pytest

from astrid.core.execution import custody_broker as custody


@pytest.fixture
def graph(tmp_path, monkeypatch):
    identities = {pid: {"pid": pid, "uid": 501, "birth_id": f"birth-{pid}"} for pid in (101, 102, 103)}
    tokens = {pid: {"pid": pid, "uid": 501, "pidversion": pid + 1,
                    "sha256": f"sha256:token-{pid}", "words": [pid] * 8} for pid in identities}
    monkeypatch.setattr(custody, "audit_token_details", lambda words: {k: v for k, v in tokens[words[0]].items() if k != "words"})
    observe = identities.get
    actors = {pid: custody.AuthenticatedCleanupActor(lambda pid=pid: tokens[pid], observe) for pid in (101, 102)}
    authority = custody.RoleCustodyAuthority(tmp_path / "scope", "engine", timeout=0.2)
    authority.designate_pending(actor=actors[101], identity=identities[103], token=tokens[103], owner_epoch="runtime-A")
    authority.bind_target(actor=actors[101], generation=1, identity=identities[103], token=tokens[103])
    return authority, actors, identities, tokens


def transfer(graph, *, transition_id="handoff-1", dead_actor=None):
    authority, actors, _, _ = graph
    return authority.transfer(actor=actors[102] if dead_actor else actors[101], next_actor=actors[102], generation=1,
                              transition_id=transition_id, owner_epoch="runtime-B", dead_actor=dead_actor)


def fire(graph, *, actor_pid=101, generation=1, observe=None, signal_provider=None):
    authority, actors, identities, _ = graph
    authority.signal(actor=actors[actor_pid], generation=generation, expected_target=authority.reference()["target"],
                     signum=signal.SIGTERM, identity_provider=observe or identities.get,
                     signal_provider=signal_provider or (lambda words, signum: None))


def test_pending_cleanup_owner_is_durable_but_cannot_signal(tmp_path, graph):
    _, actors, identities, tokens = graph
    authority = custody.RoleCustodyAuthority(tmp_path / "pending", "host")
    authority.designate_pending(actor=actors[101], identity=identities[103], token=tokens[103], owner_epoch="runtime-A")
    with pytest.raises(custody.CustodyError, match="pending"):
        authority.signal(actor=actors[101], generation=1, expected_target=authority.reference()["target"],
                         signum=signal.SIGKILL, identity_provider=identities.get, signal_provider=lambda *_: pytest.fail("pending signal"))


def test_pre_spawn_obligation_binds_only_exact_admission_before_exec(tmp_path, graph):
    _, actors, identities, tokens = graph
    authority = custody.RoleCustodyAuthority(tmp_path / "launch", "relay")
    authority.reserve_before_spawn(actor=actors[101], owner_epoch="runtime-A", admission_id="launch-1")
    assert authority.reference()["target"] == {"admission_id": "launch-1"}
    with pytest.raises(custody.CustodyError, match="differs"):
        authority.bind_pending(actor=actors[101], admission_id="other-launch", identity=identities[103], token=tokens[103])
    authority.bind_pending(actor=actors[101], admission_id="launch-1", identity=identities[103], token=tokens[103])
    authority.bind_target(actor=actors[101], generation=1, identity=identities[103], token=tokens[103])
    with pytest.raises(custody.CustodyError, match="differs"):
        authority.bind_pending(actor=actors[101], admission_id="launch-1", identity=identities[103], token=tokens[103])


def test_signal_holds_shared_guard_until_kernel_call_completes(graph):
    authority, _, _, _ = graph
    in_kernel, release, attempted, transferred = (threading.Event() for _ in range(4))
    events = []

    def kernel(*_):
        in_kernel.set()
        assert release.wait(1)
        events.append("kernel-complete")

    def move():
        attempted.set()
        result = transfer(graph)
        events.append("transfer-durable")
        transferred.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        signaling = pool.submit(fire, graph, signal_provider=kernel)
        assert in_kernel.wait(1)
        moving = pool.submit(move)
        assert attempted.wait(1)
        assert not transferred.wait(0.03)
        release.set()
        signaling.result(timeout=1)
        assert moving.result(timeout=1)["generation"] == 2
    assert events == ["kernel-complete", "transfer-durable"]
    assert authority.reference()["generation"] == 2


@pytest.mark.parametrize("actor_pid,generation", [(101, 1), (101, 2), (102, 1)])
def test_old_actor_or_generation_cannot_signal_after_transfer(graph, actor_pid, generation):
    transfer(graph)
    with pytest.raises(custody.CustodyError, match="stale"):
        fire(graph, actor_pid=actor_pid, generation=generation, signal_provider=lambda *_: pytest.fail("stale signal"))
    fire(graph, actor_pid=102, generation=2)


def test_transfer_before_durable_commit_leaves_old_owner(graph, monkeypatch):
    authority, _, _, _ = graph
    write = authority._write
    monkeypatch.setattr(authority, "_write", lambda _: (_ for _ in ()).throw(OSError("before write")))
    with pytest.raises(OSError, match="before write"):
        transfer(graph)
    assert authority.reference()["generation"] == 1
    fire(graph)
    monkeypatch.setattr(authority, "_write", write)
    assert transfer(graph)["generation"] == 2


def test_commit_then_lost_ack_recovers_exact_durable_replay(graph, monkeypatch):
    authority, _, _, _ = graph
    write = authority._write

    def lose_ack(record):
        write(record)
        raise OSError("lost ACK")

    monkeypatch.setattr(authority, "_write", lose_ack)
    with pytest.raises(OSError, match="lost ACK"):
        transfer(graph)
    assert authority.reference()["generation"] == 2
    monkeypatch.setattr(authority, "_write", lambda _: pytest.fail("replay must not recommit"))
    ack = transfer(graph)
    assert transfer(graph) == ack
    with pytest.raises(custody.CustodyError, match="conflicts"):
        authority.transfer(actor=graph[1][101], next_actor=graph[1][102], generation=1,
                           transition_id="handoff-1", owner_epoch="different")


class Child:
    pid = 101
    exit_code = None

    def poll(self):
        return self.exit_code


def test_positive_owned_child_exit_allows_recovery_but_unknown_does_not(graph):
    child = Child()
    retained = custody.RetainedChildActor(child, graph[1][101])
    with pytest.raises(custody.CustodyError, match="positive"):
        transfer(graph, dead_actor=retained)
    child.exit_code = 0
    assert transfer(graph, dead_actor=retained)["generation"] == 2
    fire(graph, actor_pid=102, generation=2)


def test_pid_reuse_or_absence_never_creates_actor_death_proof(graph):
    _, actors, identities, _ = graph
    child = Child()
    retained = custody.RetainedChildActor(child, actors[101])
    identities[101] = {"pid": 101, "birth_id": "reused", "uid": 501}
    with pytest.raises(custody.CustodyError, match="positive"):
        transfer(graph, dead_actor=retained)
    identities.pop(101)
    with pytest.raises(custody.CustodyError, match="positive"):
        transfer(graph, dead_actor=retained)
    assert graph[0].reference()["generation"] == 1


@pytest.mark.parametrize("observed", [None, {"pid": 103, "birth_id": "reused", "uid": 501}])
def test_unknown_or_reused_target_never_signals(graph, observed):
    with pytest.raises(custody.CustodyError, match="unknown"):
        fire(graph, observe=lambda _: observed, signal_provider=lambda *_: pytest.fail("unknown target signal"))


def test_runtime_epoch_transition_does_not_seize_delegated_roles(graph):
    authority, actors, identities, tokens = graph
    relay = custody.RoleCustodyAuthority(authority.root, "relay")
    relay.designate_pending(actor=actors[101], identity=identities[103], token=tokens[103], owner_epoch="runtime-A")
    relay.bind_target(actor=actors[101], generation=1, identity=identities[103], token=tokens[103])
    relay.transfer(actor=actors[101], next_actor=actors[102], generation=1, transition_id="runtime-transfer", owner_epoch="runtime-B")
    assert relay.reference()["generation"] == 2
    assert authority.reference()["generation"] == 1
    fire(graph)


def test_bounded_exclusive_lock_timeout_leaves_authority_unchanged(graph):
    authority = graph[0]
    with authority._guard(exclusive=False):
        with pytest.raises(custody.CustodyError, match="timed out"):
            transfer(graph)
    assert authority.reference()["generation"] == 1
