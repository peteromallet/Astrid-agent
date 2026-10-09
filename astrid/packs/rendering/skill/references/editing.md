# Edit a timeline: the front door

A narrated film runs on its words. You edit a **working copy** (a draft of the published
timeline), look at it, then publish it. Times are timeline seconds; *when* things happen is
written as **moments** (`on "viral"`), so a new take moves everything with its words.

## The loop
```bash
timelines checkout TL --project P                      # your working copy; it prints the next steps
timelines show TL --project P --as sheet --range c30..c31 > cut.sheet   # read: the cut sheet
#   change a line of cut.sheet (e.g. `for 1.9s` → `until "Astrid"`), then:
timelines apply TL --project P cut.sheet               # the change in plain words, check, lint
timelines visualize TL --project P --preset motion --at Astrid      # frames of it (--compare published: before/after)
timelines status TL --project P                        # every unpublished change, in words
timelines publish TL --project P -m "cover until Astrid"
```
`show`/`visualize`/`lint`/`diff` read the working copy (line 1 says so; `--published` for the live one). `discard` drops it.

## Moments (one grammar everywhere)
`"viral"` · `after "Astrid"` (the word's end) · `"tool" in v20a #2` (scope a line, nth time) ·
`beat 2 after "Astrid"` · `downbeat 1 after "x"` · `c22` (a cut starts) · `end` (own cut ends) ·
`+0.8s` alone = after the clip's cut starts · any of these `+2f` / `-0.1s`.
A clip has `on` (start), and `until` (end moment) or `for 1.9s` (a literal length); no `until`/`for` = to its cut's end.

## The sheet
```
 91.50 ┃ c30   on "And" in n21                                    · 2.50 s     ← cut id and its moment
         sprite  cover   sprite  MYSTERY  until "Astrid"  enter=stamp scale=5 x=226 y=33
 93.50   chrome  icon    sprite  ICON     on "Astrid"     scale=1 x=225 y=35
         why: held on the covered tool; on "Astrid" the cover drops.
```
A layer line: `track name element [ASSET] ["text"] [on …] [until …|for …] [k=v …]`. Change any part; a new
name adds a layer; a deleted line removes it. In `lines`, change a line's text or `gap`. Ignored when applied:
the time gutter, `· 2.50 s`, `> words`, `# chapter`. `k=…` (too long to print) stays as is; `k=ƒ` is computed
by a formula; `~k=v` is information. `apply` names the line and what to write when something is wrong.

## The same edit as a verb or in Python
```bash
timelines edit TL --project P --clip c30.cover --until Astrid   # also --on, --for, --set k=v, --swap-asset KEY|FILE, --nudge-frames N
```
```python
from astrid.sdk.timeline_checkout import Checkout
tl = Checkout.draft("P", "TL")                 # the working copy (created from the head if missing)
tl.clip("c30.cover").until("Astrid")           # or .on('"viral" +2f'), .hold_for(1.9), .set(x=230), .swap_asset("KEY")
print("\n".join(tl.changes() + tl.check().brief())); tl.save()
```
Find things: `tl.clip("c30.cover")` (cut.layer, a layer name, asset key, clip id; ambiguity lists choices),
`tl.cut("c30")`, `tl.word("viral")`, `tl.at("1:02")` (what is on screen and said), `tl.clips(cut="c30")`, `tl.orphans()`.

## The narration (re-flow)
```bash
timelines edit TL --project P --line n21 --take n21.wav --words n21.words.json --text "…"   # a new take
timelines edit TL --project P --insert-line n21b --after n21 --take … --words … --text "…" --gap 0.4
timelines edit TL --project P --remove-line n21    ·   --gap-after n21=0.85    ·   --from-script vo.json
```
Everything after a changed line moves with it; clips on words follow their words; music is cut on a beat
(the seam is reported). Moments whose word is gone are **orphans**: the sheet lists them; re-home or remove each.

Publish never overwrites a change published after your checkout: it merges other clips' changes and names
any clash. New media: `--swap-asset FILE`. Low-level document reference: [document-checkout](document-checkout.md).
