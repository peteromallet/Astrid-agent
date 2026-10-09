# Storyboards

`storyboards/astrid-intro.storyboard.json` is the authored input spec for the intro video:
per-section nav state, image variants (asset/gen), VO text + audio asset, provenance.

## Usage
    ASTRID_PROJECTS_ROOT=<root> python3 scripts/build_storyboard.py validate --story <file>
    ASTRID_PROJECTS_ROOT=<root> python3 scripts/build_storyboard.py compile --story <file> \
        --vo-align <plan.json> --out <dir>
    # then: python3 -m astrid timelines create --project <project> <slug>   (empty head; add shots with
    #       `timelines shots` and `timelines shots text set`; the compiled JSON is not loaded whole)
    #       python3 -m astrid timelines render --project <project> <slug>

Rules: the storyboard file is an authored INPUT spec (content + provenance); compiled
resolution fields and all durable execution facts live in the kernel timeline (ONE store).

## Validation
    ASTRID_PROJECTS_ROOT=<root> python3 scripts/build_storyboard.py validate --story <file>

## Timing
By default sections hold for `meta.timing.default_hold` seconds. Pass `--vo-align <plan.json>` to snap section starts/durations to VO segment timings instead.
