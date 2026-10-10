"""S28: a layer that continues after a blink (two contiguous clips) has one entrance, not two."""
from astrid.packs.rendering.executors.timeline_visualize.motion import lint, model


def _sprite(cid, start, end):
    return model.Element(id=cid, type="am-sprite", track="sprite", start=start, end=end,
                         params={"enter": "stamp"}, clip={"id": cid, "clipType": "am-sprite", "params": {"enter": "stamp"}},
                         asset="MINK")


def test_a_chained_segment_is_not_an_entrance():
    cut = {"index": 1, "start": 0.0, "end": 4.0, "clip_id": "plate"}
    first, second = _sprite("mink-a", 0.5, 2.0), _sprite("mink-b", 2.0, 3.5)
    events = [e for e in lint._accent_events(cut, [first, second], 30.0) if e.kind != "cut"]
    assert not any(abs(e.t - 2.0) < 0.05 and e.kind != "key" for e in events)
    assert any(abs(e.t - 0.5) < 0.05 for e in events)  # the real entrance is still there
