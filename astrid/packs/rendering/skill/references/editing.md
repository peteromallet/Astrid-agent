# Edit a timeline: the front door

A narrated film runs on its words. You edit a **working copy** (a draft of the published timeline),
look at it, then publish it. Times are timeline seconds; **positions are canvas px** for every element.
*When* things happen is written as **moments** (`on "viral"`), so a new take moves everything with its words.

## The loop
```bash
timelines checkout TL --project P                      # your working copy; it prints the next steps
timelines show TL c41.mink --project P                 # ONE thing's whole record: every param (typed, canvas px,
                                                       #   formula, default, allowed keys), its moments in seconds
timelines show TL --project P --as sheet --range c30..c31 > cut.sheet   # several cuts as an editable sheet
timelines apply TL --project P cut.sheet               # change a line of the sheet, then apply it
timelines visualize TL --project P --preset motion --at '"Astrid" in n21'   # frames (every frame in a 1 s window)
timelines status TL --project P   ·   timelines undo TL --project P   ·   timelines publish TL --project P -m "…"
```
`show`/`visualize`/`lint`/`diff` read the working copy (line 1 says so; `--published` for the live one).
`timelines find TL "the conclusion"` (also `--text "Astrid."`, `--asset ROCKET`, a layer name) → addresses and times.
**Make one timeline equal another** (a finished duplicate → the film): `timelines checkout FILM --project P --from
round2[@rev]`, then publish. Narration, cuts, clips and intent, assets and beats, chapters and slots all come along.

## Addresses (the same everywhere: show, edit --clip, visualize --at/--highlight, Python)
`c41.mink` (cut.layer; a layer carried into a later cut also answers to it: c30.cover = c29.cover) ·
`c41.mink.x` (one param) · `c30` (a cut; `c30..c31` inclusive) · `"Building" in v27 #2` (a spoken word) ·
`93.5` · an asset key · a layer name. Ambiguous names list their choices; outputs print the canonical address.
`c1` and `c01` are the same cut. In Python, `tl.time(X)` gives the seconds of any of these, or of a moment.

## Moments
`"viral"` · `after "Astrid"` (word end) · `"tool" in v20a #2` · `beat 2 after "Astrid"` · `c22` · `end of c30` (=`c30.end`) ·
`end` (the clip's cut; with no clip, the film) · `+0.8s` (after the clip's cut starts) · any of these `+2f`.
A clip has `on`, and `until` or `for 1.9s`. A param takes a moment too: `--set 'states[3].at="adapt" in w05c'`.
Beats come from the music clip's grid: `--swap-asset run:<id>/music` brings the run's beats; `--beats FILE|HANDLE`.

## The sheet
```
 91.50 ┃ c30   on "And" in n21                                    · 2.50 s     ← cut id and its moment
         sprite  cover   sprite  MYSTERY  until "Astrid"  enter=stamp x=1356 y=198
         sprite  tool    sprite  TOOL-16  on "Building"   x=ƒ(B2-HAND -42) y=ƒ(B2-HAND -384)
```
A layer line: `track name element [ASSET] ["text"] [on …] [until …|for …] [k=v …]`. A partial sheet is safe:
apply changes ONLY the cuts in the file; a layer line deleted from one of those cuts removes that layer.
A brand-new layer is a new name: `sprite  paw  sprite  PAW  on "Building" in v27  x=ƒ(B2-HAND -177.5) y=600`
(element: sprite, type, pixel-shape, footage, …; fractional ƒ offsets are fine). Stacking is by TRACK, not line
order: chrome > type > fx > sprite > plate; to put a layer behind another, give it a lower track (to be sure,
not the same one). A sprite's `scale` is canvas px per art px (default 6); a pixel-shape's `px_scale` is the same
for its cells. The film line ends `· v 1a2b…`: the version you exported. If the working copy was edited since,
apply merges: lines you did not change keep the newer value; a line changed both places refuses (re-export).
A NEW cut: a header `┃ c05b on "word"` with a `plate` line (its picture) and any layers; the cut before ends there.
Or `timelines edit TL --split c33 --on MOMENT` (`--add-cut --on M [--after c33]`; Python `tl.cut("c33").split(on=…)`,
`tl.add_cut(…)`): later layers move with it, spanning ones carry over it. `…` values in a sheet are kept as they are.
`tl.add("am-type", at='"Building" in v27', layer="title", hold=1.2)` joins the cut on screen as `cNN.title`.
`x=ƒ(MARK ±px)` follows a slot's mark: a plain number replaces it (and says so). `k=…` is unchanged, `~k` is info.
The `sound` section lists the music bed and other audio in no cut: edit its asset, `for`, `volume=` there.
A cut's notes: `why: …` and `hold: deliberate` (lint's HOLD/STILL leave it; `hold: off` clears), or `edit --cut c33
--why "…" --hold deliberate`. JSON params go in one token: `inset={"x":12,"y":40}`; a nested one: `--set lines[0].at=…`.
Units are the element's own declaration (element.yaml `metadata.units`): positions canvas px, `…at` params clip frames.

## The same edit as a verb or in Python
```bash
timelines edit TL --project P --clip c30.cover --until Astrid   # --on, --for, --set k=v, --swap-asset, --clear-asset, --remove
```
```python
from astrid.sdk.timeline_checkout import Checkout
tl = Checkout.draft("P", "TL")                 # the current working copy (Checkout.draft("P", "TL", "name") for another)
tl.clip("c30.cover").until("Astrid"); tl.clip("c41.tool-16").set(x=1200)   # canvas px
print("\n".join(tl.changes() + tl.check().brief())); tl.save()
```

## The narration (re-flow)
`timelines edit TL --project P --from-script vo.json --takes DIR` (DIR has `<id>.wav` + `<id>.words.json`;
`--script-gaps` uses the script's gaps) · `--line ID --take … --words …` · `--insert-line` · `--remove-line`.
Everything after a change moves; clips on words follow them; the music bed stays whole. A removed line's
clips are kept as **orphans** (status, sheet and check list them; publish waits): `--cut c07 --on '"word" in w05'`
re-homes a cut with its layers, `--clip X --on …` one layer, `--keep` accepts it in place, `--remove` drops it.

Publish never overwrites a change published after your checkout (it merges other clips and names clashes).
Two writers on one working copy are safe the same way: a save merges the other's edits, or refuses on a clash.
`edit`/`apply` exit 0 once saved; what still blocks publishing is check's (printed) state: `timelines check TL`
shows only lint that is new since checkout (`--all` for everything).
