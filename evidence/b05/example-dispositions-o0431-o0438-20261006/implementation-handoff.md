# B05 Example Migration Handoff — O0431–O0438

Candidate base: `9be144c4f2f63216e693a1a13e42cd79d3845845`.

Implemented the Astra-approved migration of the four teaching roots to v3.
The roots remain under `examples/packs/` and are still excluded from Runtime
discovery. No M04 or M21 reserved source/test paths were touched. The central
B2 test now has distinct assertions for migrated v3 examples and the remaining
v2 example/legacy smoke fixture.

## Semantic decisions

- `minimal` keeps its pack ID and demonstrates two ordinary actions; the
  trailer action calls the declared ingestion action through `astrid.invoke`.
  Empty legacy role directories are removed.
- `media` keeps its pack ID, asset ingestion/trailer behavior, brief schema,
  example brief, and title-card implementation. The title-card files move
  unchanged beneath the v3 `rendering/` resource root. The README now invokes
  both actions through `astrid.sdk.invoke` and no longer presents private
  script paths as public entrypoints.
- `file_summarizer` keeps its ID, fixtures, goldens, and the four historical
  plan modules unchanged. New public actions inspect text, validate caller
  summary counts/notes, and return a deterministic or caller-provided verdict.
  The skill documents the caller review boundary; it explicitly makes no
  pause/ack or lifecycle-parity claim. The old plan modules remain legacy
  orchestration teaching artifacts outside the v3 action catalog.
- `text_review` keeps `text_review.file_audit`, fixture/golden files, and the
  original `file_audit.py` teaching artifact unchanged. A new deterministic
  summary action and the caller-supplied audit action expose the two stages;
  documentation states that the caller reviews between calls and that v3 does
  not provide the old pause/ack lifecycle.
- The global v2 loader, `text_digest`, and local effect smoke fixture are
  unchanged. Product pack/runtime discovery is unchanged.

## Validation performed

- Static pack validation passed for `file_summarizer`, `media`, `minimal`, and
  `text_review` using `python3 -m astrid.core.pack.cli validate`.
- Scoped `git diff --check` passed.
- No pytest run or action-runtime invocation was performed. The central B2
  test still needs its scheduled focused execution, and the actions need
  behavior-level proof under the reserved B05 validation/capacity policy.

## B01 source-bound reconciliation handoff

O0431, O0434, O0435, O0436, O0437, and O0438 are resolved by the postimage
v3 `pack.yaml` declarations for their exact roots below. O0432 and O0433 are
resolved by the postimage Media README's public SDK invocations. The old
executor/orchestrator declaration roots and README private-script invocations
were removed. B01 should reconcile these exact IDs against a refreshed current
source inventory; this handoff does not claim whole-corpus closure.

## Exact path preimages and postimages

`ABSENT` denotes a path that did not exist before the migration or no longer
exists after it. Hashes are SHA-256 of file bytes. For docs and the central test,
preimage hashes were recorded from the live candidate immediately before this
edit batch; other tracked preimages match the candidate base above.

| Path | Preimage SHA-256 | Postimage SHA-256 |
|---|---|---|
| `docs/packs/creating-packs.md` | `8f885419f5bf6e3ff4286ff439c72ffbb77484578dd12001ac0e102cd767b953` | `021585a9a6a99bbdb9067ecf81b2b85fb63982cdfc0427c946b0bd824291bab4` |
| `docs/packs/pack-taxonomy.md` | `08066f29930a954aeb529563f9ce27112e94f16a6a527107a063bc9893724552` | `05040ac127aead9d037cb13e8565f1d89e98d5fb607895d579fdb6ebff9b7c49` |
| `examples/README.md` | `35da9f21e05b1b7d51b095459d940c09673f3160abd4044da279009a62cc3f8f` | `40a0f40a3a8c25bd9ad832ad30fe0b360096450eac95a81cd7fee9e682608e3e` |
| `examples/packs/file_summarizer/AGENTS.md` | `3fe27601849ee71e702348e36c84b3e83465b740ce77471c501bf0af468d4600` | `4c473c8916685d3cb519f60e65e732a023b5f627aabf66c899dcef0e046ade8d` |
| `examples/packs/file_summarizer/README.md` | `ba0950d4d90c9475dcf9256b1d01242643f862e416806b6a35c4a3df4bda060c` | `04824b131f6021d9abe9dbe203bb2c03c8cf174297dfd8b9627514057e73ed93` |
| `examples/packs/file_summarizer/STAGE.md` | `af636ed4789009a4ddb7e7fef94341477c0757d1df62b141912d303693c7dc2c` | `cfdd80cdd6f50d68d5930f03d299645f8051afc7f5e84e04768e7bad4a07e13f` |
| `examples/packs/file_summarizer/actions/accept_summary.py` | `ABSENT` | `d1d1eeb206c74db7018aa3b344de4ab36aa59e89d47b9f3c793434d661be9e4e` |
| `examples/packs/file_summarizer/actions/inspect_text.py` | `ABSENT` | `eb7a6f81d2f85a679665ec44107c2b821addebbbbfe81fff03fc415d40a1ac6a` |
| `examples/packs/file_summarizer/actions/write_verdict.py` | `ABSENT` | `0d4dc5e21fb2f43c63de7657c4253b71199baea7232c9da54cc6d9510dc46f99` |
| `examples/packs/file_summarizer/actions/write_verdict/STAGE.md` | `ABSENT` | `1250fa3f0e621328946117855a35613eb04069253b2479df4845c4c546e24b46` |
| `examples/packs/file_summarizer/docs/SKILL.md` | `ABSENT` | `5d2846fbdd5688667c1623169b06d7bd9feb7b21db0b94668feacb6fe16b6bb8` |
| `examples/packs/file_summarizer/executors/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `ABSENT` |
| `examples/packs/file_summarizer/pack.yaml` | `ddd4522296f1b0198692af2fae325e6422a399de9dfc7f8fa2c98ac3b2d594c9` | `6bfa86c749c40bcf7caa1c917ee836f156cf94789a3948d2f91b8139d2c92ddb` |
| `examples/packs/media/AGENTS.md` | `578a3d8a519bb66fdfdabb4548c1320a451b1d52c10532dcddda33b7e231e165` | `808f8e1e2550ed292fea99e3718e463d10b39ed6f13a3fca5175c5b2b3eb6edc` |
| `examples/packs/media/README.md` | `1d915471da4fa6818fee8ba8b4227ba35d909f45bea80ee0bcc11b720b2a000f` | `7f2b0d9a7970346f52073b65864b76a5930812057f324effe912fc0a0e7043aa` |
| `examples/packs/media/actions/ingest_assets.py` | `ABSENT` | `3fce389a6fd2353cc637f403c28af78991bb02c92ac4e35956c07a1b6f18ea27` |
| `examples/packs/media/actions/make_trailer.py` | `ABSENT` | `7fc8e7a8283d1e5d78fe8ba8b4227ba35d909f45bea80ee0bcc11b720b2a000f` |
| `examples/packs/media/actions/make_trailer/STAGE.md` | `ABSENT` | `97c7eecda2870d2cb842cba53cfb1ac5e8ef244f377c096923c4fea7c9249a4e` |
| `examples/packs/media/docs/SKILL.md` | `ABSENT` | `d46f20e7cac35b464b8ea5d3bf6ec92628e12dd63d46375ac34f1cd0cfa9d9b9` |
| `examples/packs/media/elements/effects/project-title-card/component.tsx` | `ce59c379f6c957b1a51e9a13c2e47e766ebcc3afd1e170847989973b11931fea` | `ABSENT` |
| `examples/packs/media/elements/effects/project-title-card/element.yaml` | `2a1bbf32597e3e5af56123003c8cb32c090931e2a472b3323c4f231839ac67e7` | `ABSENT` |
| `examples/packs/media/executors/ingest_assets/STAGE.md` | `74957d2cada7f39c7ee5f8fa7a1655cbebff5bcf6c96b06066dcbcd761798c32` | `ABSENT` |
| `examples/packs/media/executors/ingest_assets/executor.yaml` | `3af725374d02206b39f25eb38fdc1f398cf41200b9b8f06bbcbd1a3591e6b0bc` | `ABSENT` |
| `examples/packs/media/executors/ingest_assets/run.py` | `5238c64a7fa08adffd172f6b6a9c7121d449145db5f7fe5cf6697658b0a36cd6` | `ABSENT` |
| `examples/packs/media/orchestrators/make_trailer/STAGE.md` | `170e0d9eba1669128e59a93ce64beacd49f03e17ba967a71840bca7b40b96517` | `ABSENT` |
| `examples/packs/media/orchestrators/make_trailer/orchestrator.yaml` | `b4aca7bbcc1c9c821b6dc433f58dfed93a7c09ff428c1ad2f039a177a7602bd9` | `ABSENT` |
| `examples/packs/media/orchestrators/make_trailer/run.py` | `f513d4a291e9e5a3e345317de7f1b654712af6400a7a291588e586a77578cd58` | `ABSENT` |
| `examples/packs/media/pack.yaml` | `5c6f765e9fb32e7e2e5c2e5d8103e2e614efe4511c7568eca6f2fdccfab1898a` | `86560becafec6873f8349b5ff00a41369af5e00b2668784e7c325efd4758454b` |
| `examples/packs/media/rendering/elements/effects/project-title-card/component.tsx` | `ABSENT` | `ce59c379f6c957b1a51e9a13c2e47e766ebcc3afd1e170847989973b11931fea` |
| `examples/packs/media/rendering/elements/effects/project-title-card/element.yaml` | `ABSENT` | `2a1bbf32597e3e5af56123003c8cb32c090931e2a472b3323c4f231839ac67e7` |
| `examples/packs/minimal/AGENTS.md` | `8ace89dfe6c0128d4ec2c1d6fc7c9574847a84d8e45f9ad173d9cf33a0070401` | `3a0b445e02855391259753b3ae9b530725c65f12b5cba5347b2904d833207cfa` |
| `examples/packs/minimal/README.md` | `4bacb78208f566f46bdb3482b5af7bbafe4f140d3b1a2c9c8f7688ff3960760f` | `5a51d64e06ded388c5e2416e8079db20098c186b1291b831fe0364a3d36dd639` |
| `examples/packs/minimal/actions/ingest_assets.py` | `ABSENT` | `084db09a5e5ea9ddb34c32e56732e219b5296602ec47acb19fe7ba9148c2e26a` |
| `examples/packs/minimal/actions/ingest_assets/STAGE.md` | `ABSENT` | `07cb7e05cbcf7a618237bddc24c07157737313a44a65e0346bef2418046b6865` |
| `examples/packs/minimal/actions/make_trailer.py` | `ABSENT` | `498dabf34221445425f5452c20f276d727973689a2f3aca370d76a2be0b7b98f` |
| `examples/packs/minimal/actions/make_trailer/STAGE.md` | `ABSENT` | `a90dd97bad46d0fcf059a94e19cc704f9390a709fa348df799a20994fb811664` |
| `examples/packs/minimal/docs/SKILL.md` | `ABSENT` | `b3ff9409b5bd00aa91c1110e0f3d34077c26d448347bba53c0fbc8a3fce8ade0` |
| `examples/packs/minimal/elements/.gitkeep` | `fc01dfd54bd2ae94b38404ac3c586b3865a2434425aa0675a79a5baa0888740e` | `ABSENT` |
| `examples/packs/minimal/executors/.gitkeep` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `ABSENT` |
| `examples/packs/minimal/executors/ingest_assets/STAGE.md` | `8d5b81429b1f93a43b5e9adfe456c3d6db33502b0368b1078a006e68cbf0049e` | `ABSENT` |
| `examples/packs/minimal/executors/ingest_assets/executor.yaml` | `8d3324bb3f5c63cf948ef7e2e4f1b0d7d038c68623cfefb87f20b4750b7624a7` | `ABSENT` |
| `examples/packs/minimal/executors/ingest_assets/run.py` | `8796bea0426493c7ea57eabf0022358058ebb4c601b9bb3bd784376aab3a4e83` | `ABSENT` |
| `examples/packs/minimal/orchestrators/.gitkeep` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `ABSENT` |
| `examples/packs/minimal/orchestrators/make_trailer/STAGE.md` | `65302c732401e340d3e3ea0966d6e275819e920a655f7249f31fca3ae1e72076` | `ABSENT` |
| `examples/packs/minimal/orchestrators/make_trailer/orchestrator.yaml` | `d9e603247616e91f84deec20d6a3078d38cf415dc0abd85fa969d426380d5e67` | `ABSENT` |
| `examples/packs/minimal/orchestrators/make_trailer/run.py` | `c79bee589e9bf6869f39b02d2fb5c2b608dd4982a633a54ecf6fb87adf9be007` | `ABSENT` |
| `examples/packs/minimal/pack.yaml` | `9fcce047a0f347358893ef562e63a921dd76ccccfcb99cd84107c923ecd5195a` | `13c7658fc1dfd9cec21355f89df0c0d1d534bdb9058063190f1b7091ec04af24` |
| `examples/packs/text_review/AGENTS.md` | `7c509e0be86d4a0dc8b7c756c67a274aebb21c57d0c13cf37f80c1dda6577ca3` | `51d765cb4b4886928ab7d941571e7eed4f5bb87fee6ec0c4fb1c7d861efd320b` |
| `examples/packs/text_review/README.md` | `224855f5ba4d83875b7303b1918acddc01f9651228c4e957e907dc39557dde31` | `362cd15c3399037cc25637661b631216dac262efd67bfe2e873a882f21b6cf52` |
| `examples/packs/text_review/STAGE.md` | `39573b127e9df89da9628249f0dd5ff8b06d3ca7ec34a10eb75de93be233b7fe` | `2424c2ddc3dd831801af03f6abfd6cfa755bff3458eb52727d233eecc373b63b` |
| `examples/packs/text_review/actions/file_audit.py` | `ABSENT` | `805bce994d18d67a50c72a536c3af1ebd0c5ebabd00bd32580dcf7f22fdea1c6` |
| `examples/packs/text_review/actions/file_audit/STAGE.md` | `ABSENT` | `baa2803eebae2a79e051033285782ed3ed40ebfeb1cc36a97f7b57d51493acf4` |
| `examples/packs/text_review/actions/summarize_file.py` | `ABSENT` | `af5090d5c613b3a72d2da3b8f3f3bca112bed589e53af65d486f7cdb64f85ebd` |
| `examples/packs/text_review/docs/SKILL.md` | `ABSENT` | `71c1a8d20d2c9767d9f9f403c05aa158153d61e01aa3ae5ea3ef1ddabc686c5a` |
| `examples/packs/text_review/executors/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `ABSENT` |
| `examples/packs/text_review/pack.yaml` | `2ef11b45aad4cf29a43e3bbcfc9864bdd46ad81104ab97ffd184cdb4043e76d5` | `d5758c7f4116ccac3b874660499cad7cae51aa0bba97e2bdca5853999a023640` |
| `tests/packs/test_b2_integrated_closure.py` | `bfcfc3700bd539cd3bae92f279f6aab3b6dfbd3b28f66cb6064bad8bb19bc6e5` | `8aa81e4359dfb18b785fa4552af05931d56ba551294eb54959f23444798e19b2` |

The following source/data items were separately compared to their preimages
and are byte-identical: all File Summarizer root Python modules, File
Summarizer fixtures/goldens, Text Review `file_audit.py` and fixtures/goldens,
Media schema/example JSON, and the two moved title-card rendering files.
