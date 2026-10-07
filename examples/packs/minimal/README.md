# Minimal Example Pack

The canonical minimal v3 teaching pack. It has two declared actions: a small
directory inventory and a trailer action that calls the inventory action.

Run the static author check from the repository root:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/minimal
```

The authored guide is [`docs/SKILL.md`](docs/SKILL.md). The action contract is
in `pack.yaml`; the composition is implemented in
[`actions/make_trailer.py`](actions/make_trailer.py).
