# Matrix minkhole: editing and review workflow

For the reusable method, including collaboration and agent checks, start with [Replacing speech in an existing video](replacing-speech-in-existing-video.md). This document records the project-specific example and handoff.

Updated 11 September 2026. Project: `matrix-minkhole`. Timeline: `rough-cut`.

## Paired old-man sequence — 6 October 2026

Status: the paired still-image edit is saved and canonical readback matches
the validated candidates. The embedded workflow cards now use the same
old-man images through per-clip managed asset overrides. No new video
generation or voiceover is part of this pass. The live baseline is `matrix-minkhole` / `rough-cut`,
timeline `3f5e7ed71cf64d0e86487193ff549d93`, parent revision
`authoring-parent-revision-c529d2be9d42e0d0129fdc08e87299fa01f2a0f55d70bc234f499ea6e680d85c`.
It contains nine occurrences and ends at 43.500 seconds; the v23 description
below is historical and is not the current edit authority.

The user-supplied older man replaces Neo throughout the selected pictures.
Keep his dark flat cap, gray hair, long white beard/moustache, wrinkled face,
and dark shirt consistent. He remains realistic before contact, becomes a
pixel-art version of the same human in the affected reflection, and stays
pixelated through the desk handoff and closing. Morpheus, the blue-pill
alternative, narration, and original shot windows are preserved.

| Interval | Picture change |
| --- | --- |
| 9.750–12.167 | Realistic old man reaction; still replaces the Neo video picture only |
| 16.458–19.000 | Second realistic old man reaction; original audio retained |
| 22.000–26.917 | Old man in both glasses reflections; existing mink reveal retained |
| 26.917–28.917 | Matched pre-contact reflections, both men realistic |
| 28.917–30.917 | Screen-left old man becomes pixelated; screen-right stays realistic |
| 30.917–32.917 | Pixel old man examines his hands beside the mink |
| 32.917–34.417 | Pixel old man watches the mink jump / “Let's go!” |
| 34.417–36.417 | Pixel old man and mink run through the pixel world |
| 36.417–43.500 | Seated pixel old man watches the mink explain at the warm desk; bridge into Astrid Intro |

The bridge replaces the final slogan picture while preserving its existing
introductory audio. Astrid Intro's tutorial then continues unchanged. Its
closing keeps the guide, adds 2.5 seconds of the man applauding, retains the
four Claude/Hermes/Codex/Pi symbols with their complete existing voiceover,
and adds a three-second finale: one second of Astrid jumping into his arms,
then two seconds of the smiling man supporting exactly one orange Astrid mink.
His free hand and her paw wave to the viewer.
This follows the user’s correction to keep a single mink. The final Intro duration
is 105.696 seconds; only the final occurrence and full-length background/frame
layers extend. The embedded original-clip prep panel loops the blue-hand source
interval 79.12–80.87 instead of showing the original Neo reaction; its phase
timing and muted audio behavior remain unchanged.

Saved heads and receipts:

- Matrix: `authoring-parent-revision-edce8bb0502a552525a0047b3ab58ba9b8df4fd0975837175a612d0440654eea`;
  `minkhole-candidate.publication.json`.
- Intro: `authoring-parent-revision-1c1491d61100dd8ac0c3c1fc35e6664f7c10a318930a5b00ce78b8a2e9691f5e`;
  `intro-candidate.publication.json`.
- `readback-verification.json` confirms both parent structures, placements,
  internal clips, and selected-media registries match the published candidates.
- All existing audio object identities and source trims are preserved. Only
  the last Intro voiceover moves 2.5 seconds later with its existing logo plate.
- The discarded two-mink farewell is retained only as generation evidence;
  that intermediate image was subsequently corrected to `farewell-left.png`.

The intermediate Intro head after embedded card corrections was
`authoring-parent-revision-0a3de39fe1fa447df3685f4f2879013b194066ec0a7b186dd8efaf4219d9e66e`
(`intro-cards-candidate.publication.json`). Cards 1 and 4 use the new creature
reflection still; card 5 uses the new pre-contact still. The optional
`params.cardAssets` map stores registry keys, with URLs resolved only for
preview/render. Static defaults remain for other clips and cards. Focused
validation passed 6 Astrid staging tests and 22 Reigh resolver tests.
Original live-action Morpheus footage remains unchanged, including its tiny
inherited lens reflections; this pass replaces the eight Neo-focused pictures
and matching prominent workflow cards, rather than altering source footage.

Reference identities:

- Old-man photo: `sha256:eeae34c5fcf0a267582e55d0841f5e05dff30a23025028abfb1e40dd08998e9f`.
- Matrix project character reference: `7893cb52-df48-5055-a77a-8a113c7ab373`.
- Intro project character reference: `27a41bea-d807-5b23-97d5-ae35c7aa0aec`.
- Astrid refined-tail guide: `a1a705e6-fdbe-5447-aef3-64f98bf9d1a0`, primary image
  `sha256:6c6d8851ff3ef52fecb57aaefb4632b4f7236d6b86dc7c1fc91684d3f94995fc`.

Work artifacts and public-runtime receipts are in
`/Users/peteromalley/Documents/reigh-workspace/artifacts/old-man-paired-sequence-20261006`.
They include exact checkout baselines, the selected-source map, individual
Codex generation requests/results, image exports, candidate diffs, publication
receipts and focused readback. These are delivery evidence; the runtime remains
the timeline authority. Current Matrix narration has playable pinned audio but
no registered `voiceover_script` text bindings; the historical script below is
context, not a verified transcript of those files.

### Foreground-left room correction

The user corrected the geography after the first desk images: the man belongs
on the viewer side of the desk, screen-left and fully left of the coffee mug,
not behind the desk. `bridge-left.png` is the accepted fixed camera view of
that side of the same room: his chair/lap are visible, the mug stays on the near
desk corner to his right, and the desk extends rightward. Subsequent applause,
jump, and held-wave stills use this image as their exact camera/room reference.
No animated camera pan is requested or applied.

The final three seconds retain their existing interval: 102.696–103.696 shows
one Astrid jumping right-to-left from the desk into the man's open arms;
103.696–105.696 shows him holding her while both wave. Applause remains
93.796–96.296, and the existing agent symbols and complete voiceover remain
96.296–102.696. No audio is regenerated, trimmed, or retimed in this correction.
Both durable old-man character references now carry these placement notes.
The correction is saved, with all four generated stills visually reviewed:
`bridge-left.png`, `applause-left.png`, `jump-left.png`, and `farewell-left.png`.
Final heads:

- Matrix: `authoring-parent-revision-4ae710d37f0cdcc468178e32278fb08fdf4952d1c24d8f565213cf6fe6e59cef`.
- Intro: `authoring-parent-revision-6d6f9795d484936c750b01b927bd72c7a4c73e082c0d30bd93f39801883331e3`.

Receipts: `minkhole-spatial-candidate.publication.json` and
`intro-spatial-candidate.publication.json`. Placement metadata is recorded in
`spatial-reference-notes.json`; four depiction associations are recorded in
`spatial-reference-associations.json`. The initial photo remains primary.
No timeline animation or pan parameters were added or changed.

### Canonical pixel-form consistency

The transformation at 28.917–30.917 (screen-left lens in
`v23_reflection_after_amazed`) is the pixel-style authority. The approved
full-body standing reference is `pixel-consistency/pixel-old-man-canonical.png`.
It fixes hard stepped amber/cream/black clusters across face, beard, hands and
clothing; closer framing enlarges clusters instead of adding photographic
texture. The dark flat cap, single-breasted lapel coat with upper-thigh hem,
wrist sleeves/two weltpockets, dark collared shirt/belt, straight trousers and
sturdy laceup shoes remain consistent in every pose.

Separate canonical pixel references preserve and link the photographic forms:

- Matrix pixel form: `717e41cf-d7cb-52a2-b1be-8d8be142ffce`.
- Intro pixel form: `e81e1fbc-6f3a-595c-8980-571ef719c73e`.

Seven downstream stills have been corrected and saved using the approved
canonical image as an actual model input. The source transformation and photoreal pre-contact
pictures stay intact; all clip times/audio and fixed foreground-left room
geography are preserved. The embedded workflow card overrides show the
photoreal pre-contact images and therefore do not need pixel-form replacement.
Pixel-form publication receipts are `pixel-consistency/matrix-candidate.publication.json`
and `pixel-consistency/intro-candidate.publication.json`; exact selected-media
readback is in `pixel-consistency/readback-verification.json`.

The first mink consistency pass corrected six Matrix stills: creature reveal, before and
after miniature reflections, hands, corridor jump and run. It also fixes the
Intro jump-to-arms/held-wave markings and reuses corrected Matrix pictures for
Intro cards 1/4/5. The opening logo minks and desk bridge already match the
refined-tail design and stay selected. Source video samples contain no added
mink, so original live-action Morpheus footage remains unchanged.

The Matrix canonical Astrid reference is `9ec322ee-e0e2-52e7-91b7-b7bcdd2ebd65`,
using the same approved guide as Intro `a1a705e6-fdbe-5447-aef3-64f98bf9d1a0`.
Every corrected mink must have a continuous cream chin/throat/chest/belly,
slender long body, short legs, tiny round ears, and a thin tapered tail ending
in dark brown. No disconnected belly patch, solid-orange older design,
bushy tail, extra mink, or new black fur markings are permitted.
All eight mink corrections have been visually reviewed and saved. Final heads:

- Matrix: `authoring-parent-revision-245723de8ee5d6acec718e9307a6cabe0c664784a074184006f372675ef573b0`.
- Intro: `authoring-parent-revision-2e4986ac49099d0b1d71f011dfc3de0a5691f1193e81d9068ba63f5e38f7b827`.

Final publication receipts are `mink-consistency/matrix-candidate.publication.json`
and `mink-consistency/intro-candidate.publication.json`. All clip structures,
placements, narration bindings, source audio and timing are unchanged by both
consistency passes. Intro cards 1/4 and 5 use the corrected creature/before
images through their existing registry keys. Contact sheets for both passes
are in their respective `review-contact-sheet.jpg` files.

**Practical generation lesson:** supplying the mink guide during an old-man-only
edit did not reliably correct existing mink markings. The successful second
pass used the current full scene, canonical mink guide, and pixel-man lock as
actual image inputs, then explicitly changed only the mink. Visually check
continuous cream throat/chest/belly and a dark tapered tail tip; a foreground
orange limb can naturally occlude the cream underside and should not be
painted cream to force artificial continuity. Preserve the fixed man/scene
and review every returned image instead of assuming references guarantee
compliance. The original photographic references remain primary for the
photographic forms; separate pixel references preserve the approved costume
and style.

### Tightened paired-video intro ending — 2026-10-06

The latest Astrid intro head is
`authoring-parent-revision-b8ded5bad605fdba8e307d002cb2a9f32dcd1af1b0d9d274bdd6ca6fe4c7b79d`,
ending at93.281s (12.415s shorter). After the shorter recap, Astrid's sign
appears on“for others to use and learn from” at72.321; applause begins74.351,
four agent icons80.141, the existing jump88.691, and the new cuddle89.691.
The standalone guide image is removed from the selected ending. The four
original narration media are retained with verified silent tails trimmed;
the redundant recap lead-in is removed and its pinned text updated.

The final narration is unique“More details in the post. Feedback is hugely
appreciated”, and remains over jump/cuddle. An initial duplicate-audio inference
was wrong: identical asset keys in different shot-local registries referred to
different audio. Actual decoded audio and word timestamps, not shared key
names, determined the edit. Earlier intro shots and IntoTheMinkhole are unchanged.

Cuddle came from current-session built-in imagegen after the managed worker's
configuration synchronization failed twice before image admission. It is
truthfully imported media with canonical character associations; no successful
managed generation or unsupported generation variant is claimed. Receipts,
readback, audio evidence and exact intervals are in
`../artifacts/old-man-paired-sequence-20261006/tight-closing/README.md`
(relative to repository root). All prior sources remain available.

### Palm reveal, contact and final eye-contact correction

The first mink audit was incomplete: it omitted two selected stills inside
`03 Red pill`. It must not be treated as proof that every selected mink was
reviewed. A fresh inventory now enumerates every selected visual clip, rather
than filtering by old-man-related asset names.

The actual Red pill shot is three stills at 19–22 seconds, not a moving video:
closed fist 19–20 (no mink), open palm 20–21 (`v15_red_open`), and wave 21–22
(`v15_red_wave`). The two latter pictures retained the older solid-orange
mink and are now corrected. The next glasses reveal turns the small mink
toward the old man, seen from behind/back-three-quarter. The before/after
reflection progression shows her reaching a paw toward his index fingertip,
then making contact while preserving the photoreal-to-pixel transition.
Intro's final held-wave image gives both characters direct eye contact with
the viewer; all other Intro shots and all timing/audio remain unchanged.

Completed this correction in two canonical saves: Matrix
`authoring-parent-revision-40dc1a1e3e67f7cb390b66527f8d0b61c3fc02f1310d6b41f679a3ef0cf83e0a`
and Intro
`authoring-parent-revision-2be997920e0f76331c3ae40c3ca96379a28c2bd604e85f40ffef0f520b700c45`.
Managed Codex generated five Matrix replacements and one Intro replacement,
with source scenes and canonical Astrid guide as actual image inputs. Human
review accepted all six. The first red-open attempt timed out at 600 seconds;
a bounded retry using only the relevant hand scene and mink guide succeeded.
No moving video was generated or converted in this batch.

Evidence lives in
`../artifacts/old-man-paired-sequence-20261006/interaction-corrections/`
relative to the Astrid repository root: requests, results,
variants, six PNGs, `all-selected-visuals.json` (19 selected visual clips),
`original-media-availability.json`, two candidate/check/publication/readback
bundles, `scene-reference-associations.json` and `verification.json`.
Readback confirms five Matrix selections and one Intro selection changed;
parent layers, placements, every internal clip, timing, audio and narration
bindings remain exactly equal to their respective baselines. The publication
summary's “Other properties changed” refers to selected-media descriptor and
mirror fields (including dimensions/type/origin), not changed clip behavior.
The earlier incomplete audit claim is superseded by the 19-clip inventory.


The original complete source video remains in the managed catalog as
`matrix-red-blue-pill-edit.mp4`, object
`sha256:059624abaab9d27a3d9901e0c78961cc10c995890ecd200f254a9cbb1612cba7`
(122.044 seconds, 1920×1080, 20,805,651 bytes). Original Red pill stills remain
available too. Replacing a selection does not delete original media or earlier
immutable timeline revisions. Current evidence and generation receipts are
under `interaction-corrections`; no video generation or medium conversion is
part of this correction.

## Historical delivered result (September 2026)

The latest delivered reference-keyframe preview is **v23**, canonical render `a5f7624d22eb4e85bfc9a40aba81f76a`, digest `sha256:29f72b5caa8349afa6c6731dc6a2347264aa527737b670250bd0fa1c8eba2dcd`, approximately **43.492667 seconds** at 720p/24 fps. Parent timeline version: **21**; the final reflection child is version **16**. It was rendered through the Astrid task runtime with `review: true`, opened from the exact successful run, and checked as a reference-keyframe preview. The v23 picture updates preserve the accepted four-limb correction while matching the mink scale and the amazed pixel-Neo expression; this remains a still-based visual draft rather than finished animation or lip-sync.

```bash
python3 -m astrid runs open a5f7624d22eb4e85bfc9a40aba81f76a --project matrix-minkhole
```

The first five seconds introduce the canonical mink-on-ASTRID logo, melting pixels and a Matrix-style pullback. The final part of that pullback uses actual Morpheus speaking footage and continues into the original “You have two options” opening. The full dialogue remains present.

The current six-shot cut is: **00:00–00:05** ASTRID through the glasses; **00:05–00:08** “Two options”; **00:08–00:19** blue-pill manual tools; **00:19–00:22** red pill; **00:22–00:26.916667** creature reveal in the glasses; and **00:26.916667–00:43.492667** the reflection close-up. The separate frontal smiling Neo still remains removed. The final shot keeps the two reflections distinct: only the touching, screen-left Neo changes, while the screen-right blue-pill Neo remains human. The mink then examines its hands, says “Let’s go!”, and runs through the pixel world before the canonical Astrid logo and slogan ending.

## Current production shot approach

Treat the v23 cut as the timing and continuity reference for the next generation pass. Start with one short, representative speaking-face passage, using the actual source shot and its surrounding original audio. Resolve the replacement line, source interval, retained context and spatial mask before processing the longer reflection sequence. Keep the hand/object reveal and the two-reflection transformation as a separate pilot: it needs tracked spatial masks, matched before/after contact frames and explicit protection for the unaffected screen-right character.

The practical order is: lock the Astrid timeline and phrase timing; test one H3 audio/video inpainting passage; inspect the generated line and both audio boundaries; then test the hand/object or reflection replacement separately. Combine them only after each pass is visually stable. A still-based reference-keyframe preview can establish design and timing, but it cannot prove temporal consistency, lip-sync, identity preservation or reflection continuity.

For the complementary full-frame continuation method, use [Matrix Minkhole H3 extension video generation](matrix-minkhole-extension-video-generation.md). The first speaking-start candidates are already in the current source cut: Morpheus at source `74.9347368421–76.40s` for the short “two options” pose, and Morpheus at `81.05–84.30s` for the primary extension pilot. Use extension when the shot may move beyond that start state; use the inpainting guide when the body/pose must remain fixed.

For the current bridge test, the continuation prompt must explicitly begin
with the source dialogue **“This is your last chance.”** The experimental
follow-on line is **“You can poo or pee on my face.”** Do not replace the first
line with a placeholder: it is the audible line in the source passage and is
part of the prompt contract.

## Lessons from the reference-keyframe revisions

- **Review mode is the default during feedback.** Use `--review` on every revision and inspect the actual exported labels. The filmstrip complements the video; it does not replace those labels.
- **Review exports are lightweight by design.** Remotion uses its backend `--scale` option to fit the authored canvas inside 640x360 while preserving aspect ratio. The canonical canvas and authored positions stay unchanged, the output profile matches the dimensions actually emitted, and the video carries `Low Res Render` at top left. Explicit profiles and clean exports keep their existing resolution.
- **Find the real character in the referenced timeline.** The useful Astrid-intro references were later registered anchors, not its first opening image or the initially empty saved-reference list. `anchor_shot_v02.png` established the mink-on-logo design, `anchor_shot_v03.png` the upright mink and slogan, and `anchor_shot_v06.png` the side profile. These names describe the inspected exports; recover the managed media through the source timeline rather than treating a filename as identity.
- **Use those images as generation inputs every time.** The mink is flat orange pixel art with a long low body, short legs and dark pixel details. Earlier furry, rounded or glowing-outline substitutes did not match. Reflections must preserve the same character design.
- **Track the two reflections separately.** Screen-left is the mink interaction and transformation; screen-right retains the human blue-pill character. Keep matched before/after contact frames and preserve the unaffected figure.
- **Preserve speech while changing picture.** Removing the smiling still meant extending the preceding reflection picture, not shortening its dialogue. Adding the intro must not lose “You have two options.” Compare decoded audio when an edit is picture-only.
- **A zoom into a speaking shot needs the real shot.** The opening zoom was corrected to use advancing source footage, with matching source time across the following cut. Check adjacent rendered frames for continuity.

## Historical timing preview (v13)

The remainder records the earlier 9 September timing pass and its evidence. Its render IDs, timestamps and future-work list are historical, not instructions to restore that edit. v13 (`e3aaf311c17b450198d1c2d6f1582887`) lasted about 29.54 seconds and still used the original red-pill footage. MiniMax/H3 was investigated but was not used for these delivered revisions.

## Script and picture

| Narration | Intended picture |
| --- | --- |
| “You have two options.” | Morpheus leaning forward with both fists closed. |
| “You take the blue pill.” | Blue hand opening. |
| “You keep running tools one by one, tuning workflows by hand, searching through GitHub issues for answers.” | Neo looking nervous, then Morpheus speaking; preserve blue-only pill reflections. |
| “Or you get this cute little pixely creature,” | Hand reveal; eventually replace the red pill with the creature. |
| “who knows how to do everything in the open-source AI art space.” | Cut back to Morpheus’s face at the audible start of “who knows.” |
| “And see just how deep the minkhole goes.” | Continue through Neo picking it up; stop before the hand-to-mouth action. |

“Creature” introduces the helper without repeating “mink” before “minkhole.”

## What we used

- **Astrid CLI/SDK:** inspect canonical timelines, save version-checked edits, render, inspect run evidence and retrieve verified media. The parent timeline arranges child shots; child clips control source trims, speed and audio placement.
- **Timeline visualizer:** rendered filmstrips and contact sheets show actual output at regular intervals and around cuts. Structural views explain track and clip arrangement.
- **macOS Daniel voice, rate 135:** temporary narration. The creature sentence was generated as one continuous WAV and split at an exact audio sample across child shots, preserving its delivery.
- **Local faster-whisper:** estimated phrase/word timing, checked against the intended script and actual audio. These diagnostic estimates were not treated as canonical transcripts. The public transcription route lacked provider readiness at the time.
- **FFmpeg:** source-frame inspection, rendered-audio analysis, silence measurement and adjacent-frame verification.
- **Diagnostic sentence/waveform viewer:** placed text, picture and measured audio on a common time axis to expose pauses and cuts. This project prototype informed the integrated inspector.
- **Image generation:** produced the creature-in-hand concept still from a source frame.

## The review loop

1. Render a concrete edit with `--review`, verify visible shot names/timecodes in the video, and pin review evidence to that exact run.
2. Scan a contact sheet for picture continuity. Sample more densely around a suspicious cut; inspect the frames immediately before and after it.
3. Compare each phrase’s beginning and end with the picture. Use the waveform to distinguish an actual quiet interval from missing transcript annotations.
4. Listen around the proposed edit. ASR gives a starting point; the audible onset determines the final cut.
5. Map the render time back to its owning child clip. Adjust picture trims, speed or placement; move audio only when the spoken timing itself needs changing.
6. Save through Astrid, re-render, and compare the affected interval. Open the actual video or image for review and report exact time references.

This caught early red-pill reflections, unwanted flashes at cuts, incorrect opening footage, a short pickup ending and excess silence before the closing line. It also showed why a single image per authored shot is insufficient: a shot can contain several visual changes or span a sentence boundary.

Two verified fixes:

- **Closing pause:** v11 had 2.468 seconds of measured near-silence. Moving the closing shot and audio 50 frames earlier at 24 fps reduced it to **0.385 seconds**. Measurement used FFmpeg at −40 dB with a minimum interval of 0.25 seconds.
- **Creature reveal:** v13 holds the hand until **17.583 seconds**, then cuts to Morpheus on “who knows.” Frame 421 shows the hand; frame 422 shows his face. Decoded audio is identical to v12, preserving the tightened pause.

## Using the timeline visualizer

Run from the Astrid checkout with a compatible workspace runtime. For a broad review of the delivered edit:

```bash
python3 -m astrid timelines visualize rough-cut --project matrix-minkhole \
  --view filmstrip --render-run e3aaf311c17b450198d1c2d6f1582887 \
  --every 0.5 --columns 5 --page-size 50 --include-media --json
```

For closer inspection around the creature-to-face cut:

```bash
python3 -m astrid timelines visualize rough-cut --project matrix-minkhole \
  --view filmstrip --render-run e3aaf311c17b450198d1c2d6f1582887 \
  --at 17.583333 --context 2 --every-frames 6 --include-media --json
```

Use `--sample cuts` for cut boundaries, `--sample clips` for picture clips, or `--sample shots` for authored story beats. Interval sampling also retains adjacent cut frames. Use `--range 14..20` to restrict a review. Density controls hide/show captured samples; rerun at a finer interval to obtain additional frames.

The inspector shares an absolute time ruler across picture and declared tracks. Select a frame or interval to inspect details and copy its time, target or pinned focus command. `--include-media` bundles verified video for playback. Open the returned HTML for interactive inspection or the PNG contact sheet for a single-image overview.

Use `--view structure` when the question is how clips and tracks are arranged. Its legacy navigation targets differ from rendered-inspector targets; use the commands supplied by each view.

## Audio visualization status

The [audio integration document](../docs/plans/timeline-inspector-audio-integration.md) now records implementation and focused test evidence in the current checkout. It adds waveform, measured quiet gaps, explicitly admitted speech annotations and optional playback to the existing filmstrip inspector. Missing transcription does not block waveform inspection, and opening a view does not start a transcription provider call.

The earlier [audio-v11 diagnostic](../runs/matrix-minkhole/audio-v11/sentence-review.html) and [waveform sheet](../runs/matrix-minkhole/audio-v11/auditory-timeline.png) show the **old v11 edit**, including the long pause. They are reference evidence, not current v13 views. Integrated playback still needs a fresh Matrix/browser-backed check; implementation tests do not replace that review.

## Historical next steps after v13

1. **Review v13 in the integrated inspector.** Confirm runtime/client compatibility, generate the pinned filmstrip with media, and check waveform, phrase coverage, seeking and cut navigation against the actual video. Admit verified speech annotations if phrase rows are missing.
2. **Finish the picture timing before effects.** Use phrase boundaries and cut-neighbor frames to review the reveal, face return and pickup. Keep any new changes tied to a new render and its own evidence.
3. **Test the creature replacement on the short hand reveal.** Select and validate a video-editing workflow using the source shot and concept still. Match lighting, palm contact, scale and motion before processing other shots.
4. **Carry the creature consistently through reflections and pickup.** Nothing should appear before the reveal. Preserve the blue pill and end before any eating action.
5. **Finish dialogue and sound.** The current voice is temporary narration. If on-camera speech must match the new words, test a dialogue/lip-sync pass after timing is settled; then review final sound and picture together.
6. **Render and repeat the same evidence loop.** Check every cut, sentence transition, measured gap and effect boundary, then open the finished video for review.

Local media links above are workspace deliverables, not portable source assets. Astrid’s managed run and digest-verified artifacts remain the durable render evidence. See the [rendering skill](../astrid/packs/rendering/skill/SKILL.md) for the supported editing and visualization contract.
