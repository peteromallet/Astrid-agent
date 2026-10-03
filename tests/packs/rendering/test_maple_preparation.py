"""Strict Maple-specific preparation, without requiring the supplied archive."""
import hashlib
import re

import pytest

from astrid.packs.rendering.live_scenes.maple import prepare_maple

STARTUP = "window.__bootStage='rendering the initial frame';updateCafeLOD();syncCafeRoof();renderer.render(scene,camera);window.__bootStage='ready';window.__ready=true;"
DEFERRED = STARTUP.replace("renderer.render(scene,camera);", "")
REQUESTED = "render:()=>{syncCafeRoof();updateCafeLOD();sky.position.copy(camera.position);renderer.render(scene,camera);}"
JOURNEY = '{"duration":176.5,"fps":30,"camera":{"knots":[{"t":59.7}]},"captions":[{"start":57.1,"end":62.8,"text":"He ordered, and the food arrived."}]}'
AUDIO = '{"src":"data:audio/mp3;base64,retained-payload"}'


def fixture():
    return """<!doctype html><html><head></head><body>
<script>
function tick(){requestAnimationFrame(tick);
}
""" + STARTUP + "requestAnimationFrame(tick);\n" + REQUESTED + """
window.__assetsReady=true;window.__journeyReady=true;
const nativeTextureData='data:image/png;base64,retained-texture';
function documentCreateAudio(src){const audio=new Audio(src);audio.preload='auto';audio.volume=.88;return audio;}
w.FeedbackRecorder.install(w.MapleHollow, w.Journey);
const audio = JSON.parse(document.getElementById('mapleAudioData').textContent);
</script>
<script id="mapleJourneyData" type="application/json">""" + JOURNEY + """</script>
<script id="mapleAudioData" type="application/json">""" + AUDIO + """</script>
</body></html>"""


def test_defer_only_exact_standalone_draw_keep_native_request_readiness_and_data():
    original = fixture()
    prepared = prepare_maple(original)
    html = prepared["html"]
    assert STARTUP not in html
    assert DEFERRED in html
    assert html.count("renderer.render(scene,camera);") == 1
    assert REQUESTED in html
    assert "M.render();M.renderer.getContext().finish();" in html
    assert "J.evaluate(sourceTime);" in html
    assert "document.getElementById('journeyCaption').textContent=J.document.captions.find" in html
    assert "requestAnimationFrame(tick);" not in html
    assert "window.__assetsReady=true;window.__journeyReady=true;" in html
    assert "while(!window.__assetsReady || !window.__journeyReady)" in html
    assert "data:image/png;base64,retained-texture" in html
    for element, payload in [("mapleJourneyData", JOURNEY), ("mapleAudioData", AUDIO)]:
        assert re.search(r'<script id="' + element + r'"[^>]*>(.*?)</script>', html, re.S).group(1) == payload
    assert prepared["manifest"] == {"formatVersion": 1, "entry": "maple-hollow-v39.html", "duration": 176.5, "authoredFps": 30}
    assert "source" not in prepared
    assert "revision" not in prepared
    entry_bytes = html.encode("utf-8")
    assert prepared["entry"] == {
        "digest": "sha256:" + hashlib.sha256(entry_bytes).hexdigest(),
        "media_type": "text/html",
        "size": len(entry_bytes),
        "filename": "maple-hollow-v39.html",
    }
    assert prepared["assets"] == []
    # The sole difference from the previous prepared output is the strict
    # startup draw deletion; source payloads remain byte-identical.
    previous_prepared = html.replace(DEFERRED, STARTUP, 1)
    assert prepared["entry"]["digest"] != "sha256:" + hashlib.sha256(previous_prepared.encode()).hexdigest()
    assert previous_prepared.replace(STARTUP, DEFERRED, 1) == html
    assert prepare_maple(original) == prepared


@pytest.mark.parametrize("changed", ["missing", "duplicate"])
def test_startup_anchor_drift_fails_closed(changed):
    original = fixture()
    broken = original.replace(STARTUP, STARTUP.replace("renderer.render(scene,camera);", "/* changed draw */")) if changed == "missing" else original.replace(STARTUP, STARTUP + STARTUP)
    with pytest.raises(ValueError, match="adaptation anchor changed: window.__bootStage"):
        prepare_maple(broken)
