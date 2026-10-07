"""Bounded Maple Hollow v39 adaptation; keeps native world/journey code.

This is authoring-side preparation of supplied HTML, not an import-time build.
The output remains editable HTML/data, with embedded Three.js/textures retained.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def prepare_maple(html: str) -> dict[str, Any]:
    def replace_once(old: str, new: str) -> None:
        nonlocal html
        if html.count(old) != 1:
            raise ValueError(f"Maple v39 adaptation anchor changed: {old[:70]}")
        html = html.replace(old, new, 1)

    replace_once("requestAnimationFrame(tick);\n}", "/* Hosted: no autonomous RAF. */\n}")
    replace_once("window.__ready=true;requestAnimationFrame(tick);", "window.__ready=true;")
    # The standalone preview draw blocks bridge boot on software WebGL. Only
    # the host's explicit frame request may draw; native construction and the
    # later MapleHollow.render entry remain unchanged.
    replace_once(
        "window.__bootStage='rendering the initial frame';updateCafeLOD();syncCafeRoof();renderer.render(scene,camera);window.__bootStage='ready';window.__ready=true;",
        "window.__bootStage='rendering the initial frame';updateCafeLOD();syncCafeRoof();window.__bootStage='ready';window.__ready=true;",
    )
    replace_once("function documentCreateAudio(src){const audio=new Audio(src);audio.preload='auto';audio.volume=.88;return audio;}",
                 "function documentCreateAudio(){return {paused:true,ended:false,currentTime:0,duration:176.5,pause(){},play(){throw Error('Hosted scene is silent');},addEventListener(){}};}")
    replace_once("w.FeedbackRecorder.install(w.MapleHollow, w.Journey);", "/* Hosted: feedback/camera controls are disabled. */")
    # Retain standalone audio source data without decoding or starting it.
    replace_once("const audio = JSON.parse(document.getElementById('mapleAudioData').textContent);", "const audio = {src:''};")
    # The native installation still constructs its own disabled controls. The
    # output surface retains only the authored caption, updated by source time.
    adapter = r"""
<style>
.ui:not(#journeyCaption), #loading {display:none!important}
#journeyCaption {bottom:6%;font-size:clamp(13px,2.1vw,28px);pointer-events:none}
canvas {display:block}
</style>
<script>
(() => {
  let disposed=false;
  window.astridScene={
    async initialize(){
      const deadline=performance.now()+25000;
      while(!window.__assetsReady || !window.__journeyReady){
        if(window.__sceneError || window.__mapleBoot.error)throw Error(window.__sceneError?.message || window.__mapleBoot.error);
        if(performance.now()>deadline)throw Error('Maple assets/journey timed out');
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      window.Journey.pause();window.Journey.voiceEnabled=false;
      window.Journey.play=()=>{throw Error('Host owns scene time');};
      window.MapleHollow.keys.clear();
    },
    async render({sourceTime,width,height}){
      if(disposed)throw Error('Scene disposed');
      const M=window.MapleHollow,J=window.Journey;
      M.renderer.setPixelRatio(1);M.renderer.setSize(width,height,false);
      M.camera.aspect=width/height;
      J.evaluate(sourceTime);
      M.camera.updateProjectionMatrix();
      document.getElementById('journeyCaption').textContent=J.document.captions.find(c=>sourceTime>=c.start&&sourceTime<c.end)?.text||'';
      M.render();M.renderer.getContext().finish();
      window.__astridFrame={sourceTime,width,height,journeyTime:J.time,playing:J.playing};
    },
    dispose(){
      if(disposed)return;disposed=true;window.Journey?.pause();
      const M=window.MapleHollow;if(!M)return;
      const geometries=new Set(),materials=new Set(),textures=new Set();
      M.scene.traverse(object=>{
        if(object.geometry)geometries.add(object.geometry);
        for(const material of [object.material].flat().filter(Boolean))materials.add(material);
      });
      for(const material of materials){for(const value of Object.values(material))if(value?.isTexture)textures.add(value);material.dispose();}
      for(const geometry of geometries)geometry.dispose();for(const texture of textures)texture.dispose();
      M.renderer.dispose();M.renderer.forceContextLoss();
    }
  };
})();
</script>
"""
    replace_once("</body></html>", adapter + "</body></html>")
    # All rendering randomness in Maple uses its existing seeded random().
    # Runtime Three.js UUIDs do not affect pixels. Embedded source/data unchanged.
    match = re.search(r'<script[^>]+id="mapleJourneyData"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("Missing Maple journey document")
    import json
    duration = json.loads(match.group(1))["duration"]
    entry_bytes = html.encode("utf-8")
    return {
        "manifest": {"formatVersion": 1, "entry": "maple-hollow-v39.html", "duration": duration, "authoredFps": 30},
        "entry": {
            "digest": "sha256:" + hashlib.sha256(entry_bytes).hexdigest(),
            "media_type": "text/html",
            "size": len(entry_bytes),
            "filename": "maple-hollow-v39.html",
        },
        "assets": [],
        "html": html,
    }
