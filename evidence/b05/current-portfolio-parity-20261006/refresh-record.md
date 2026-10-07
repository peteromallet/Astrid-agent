# B05 current portfolio parity refresh

Date: 2026-10-06  
Scope: isolated Astrid candidate worktree; `tests/packs/test_portfolio_parity.py` and this evidence record only.

## Change

Updated parity expectations for the eight current v3 representatives: `rendering`, `media`, `training`, `iteration`, `youtube`, `vibecomfy`, `moirae`, and `runpod`. The pack contract assertions now require schema version 3, the declared `docs/SKILL.md` skill, at least one v3 component declaration, and existing paths for declared nested resources. Legacy `content` roots and separate v1 component manifests are no longer expected for these v3 packs.

Dispatch coverage remains enabled for all eight representatives. Their command-action projections are `rendering.render`, `media.clip_extract`, `training.search_loras`, `iteration.assemble`, `youtube.youtube_audio`, `vibecomfy.validate`, `moirae.moirae`, and `runpod.session`. The existing `_run_external_executor` stub is preserved and intercepts before provider or subprocess execution.

Resolver discovery, `validate_pack`, component declarations, resource existence, and representative pack IDs remain asserted.

## Focused verification

Exact command:

```text
/Users/peteromalley/Documents/reigh-workspace/Astrid/.venv/bin/python -m pytest -q tests/packs/test_portfolio_parity.py
```

Result:

```text
40 passed in 53.65s
```

No provider, subprocess action, network, GPU, Runtime, build, or installation was invoked by this focused test.

## Hashes

Test-file SHA-256 immediately before this refresh (the candidate file already contained earlier uncommitted v3 edits):

```text
ec75fb2081c4da86d2fe21be1b2414d454f635e32a3bb8dc6a094033f121f09e
```

Test-file SHA-256 after this refresh:

```text
deda7c55bb6195ca30f26673be0be51ec17fc469edf385227e883d608eb537f1
```

Current representative `pack.yaml` SHA-256 values:

| Pack | SHA-256 |
|---|---|
| rendering | `a8031313a42b036b9d5677e8be746f82eaa025c02666fa5c5e76b478d73a903b` |
| media | `6026981380d1174a827fac9d5b3b72581eb68195dea85429396ddd4c059d78c1` |
| training | `b5fdfe0e11c30b4f3bea97acdd657857dc1b6c150afdee6fdd16322f9e9f2b55` |
| iteration | `4c0f77eb26aaca3e6a2b0e5985587e82a70315aef835da92e63ad0afbb478b00` |
| youtube | `c4e36b291fdfa0b4f98eedfe178a2676cfc53b3238ea0b24bbb1ce24c5a01f0a` |
| vibecomfy | `a978feb918e6d0ef51b34fa60d8d1acfd4b1944814943514297af3072e58c4f2` |
| moirae | `7f846503f60aab0db45d8bba345e7d086757010c884a8fc2ef8420546b824f1a` |
| runpod | `1d40e426e70e99f3d3d9902af878df4110c17124f41d067ae69e410dfa957e65` |

## Limits

This verifies declaration resolution, validation, resource existence, and the mocked external dispatch boundary. It does not execute pack actions or establish provider/runtime behavior. No representative failed to reach the mocked boundary.
