# Frozen inputs for iteration_video (M21 reservation)

This stage documents the pack-local offline handoff and its staged command
consumer in `run.py`. The pack remains schema v2 until the whole-pack cutover;
this command is not yet declared in `pack.yaml`. The existing v2 producer and
M09 assembly remain intact.

## Staged command inputs

The command accepts explicit `--project-id`, `--target-run-id`, `--frozen-inputs`
(an admitted JSON file), `--media-dependency` (an admitted media file), and the
standard attempt output directory `--out`. `--theme` accepts a separate optional
admitted file. `--force` is an optional typed boolean that preserves the existing
`iteration.assemble --force` behavior: it bypasses only the existing 0.6 quality
floor, keeps the actual score and missing-lineage diagnostics, and records
`forced=true` in the existing assembly manifest/quality outputs. It does not
change renderer provenance or authorize any additional media. The action accepts
no Runtime endpoint, credentials, project tree or caller-selected materialization
root.

The frozen envelope must contain exactly one media binding. The consumer checks
its expected project/target and the supplied media file's actual SHA-256 and byte
size, then stages that file inside the current output directory. It performs
offline assembly using `assembly_inputs` and migrated `iteration.assemble`.
Every resulting registry asset explicitly binds `media_dependency`, retains its
exact CAS identity, and has no source file locator. Multiple historical
associations may reference that one admitted object.

The public `rendering.render` child request contains producer files for timeline,
assets_registry and media_dependency; theme is supplied only as a separate file.
The consumer requires exactly one returned video and provenance association,
materializes both through the public child result API, verifies their returned
byte identities, and copies those bytes unchanged into its output directory.
Child failure or missing/ambiguous output fails the command.

## Host preparation and authority

`admission.admit_iteration_video` now connects an explicit historical selection
to ordinary public SDK root admission. Supply an authenticated SDK client,
canonical project/run/task/attempt/association and manifest artifact index,
existing manifest and quality documents, exact parent/Rendering capability
pins, the finite discovery grant and a separate Rendering policy. Optional
`extra_pack_roots` selects a staged declaration; `execution_request` retains the
ordinary SDK placement contract. A run ID alone cannot reconstruct the legacy
graph: the existing manifest, quality and explicit association are dependencies.

The helper compares local normalized declarations and the registered capability
pins, then reads the exact project/run/task/association through an isolated
generated client. Its local raw metadata byte and row policy uses the generated
`_request(response_reader=...)` seam with finite reads, including HTTP errors;
unknown metadata counts before typed projection and a failed read stops
admission. The returned association supplies historical attempt and media CAS
identity. A newer current task attempt does not invalidate that association.
Caller document identities are consistency checks, never read authority.

Existing freeze/serialize helpers preserve content and quality diagnostics while
removing locators and projecting observational events. Only the canonical JSON
envelope is ingested through `ingest_project_object` for the selected project;
its returned object ID, digest and size must match the exact bytes, and any
returned project claim must agree. The ordinary `astrid.invoke`
call carries that descriptor as `frozen_inputs` and the singular discovery grant
inside external `child_delegation` policy. `RemoteTasks.create` derives replay
identity from the completed admission payload. This helper downloads no media
and creates no current-attempt outputs or child authority.

B01's exact bounded grant authorizes the trusted host to discover and freeze a
canonical project and exact target run. F04/F05 owns admission, live task/attempt/
lease/fence/epoch checks, cancellation and replay, object authentication, byte
verification and confined materialization. The separately pinned rendering.render
child capability and its finite dependency grant are also required.

`shared/iteration_inputs.py` reads no Runtime, credentials, environment, project
tree or files. Document authority metadata and registry entries are claims to
validate for consistency, never permission to read. No archive, embedded media,
arbitrary locator resolution or new general request framework is introduced.

## Serialized shape

One canonical UTF-8 JSON document uses schema_version 1:

```json
{
  "schema_version": 1,
  "project": "canonical-project",
  "target_run_id": "selected-run",
  "manifest": {"schema_version": 1, "target_run_id": "selected-run", "runs": [], "authority": {"kind": "runtime", "project": "canonical-project"}},
  "quality": {"schema_version": 1, "target_run_id": "selected-run", "authority": {"kind": "runtime", "project": "canonical-project"}},
  "media_bindings": [{
    "name": "image_0",
    "object_id": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "size": 1234,
    "media_type": "image/png",
    "filename": "image-0.png",
    "associations": [{
      "project": "canonical-project",
      "run_id": "selected-run",
      "artifact_index": 0,
      "task_id": "task-id-if-available",
      "output_id": "output-id-if-available",
      "source_association_id": "association-id-if-available"
    }]
  }]
}
```

The example abbreviates the content documents and uses a placeholder media
digest. A valid manifest contains the target run and its selected runs. Preserve the full
existing iteration.manifest.json and iteration.quality.json content: run order,
lineage, output metadata, supporting facts, unavailable-source diagnostics,
quality dimensions and honest missing evidence. Quality scores are neither
recomputed nor raised. An embedded manifest quality object must equal the quality
document. Both documents must bind the expected project and target; optional
manifest authority.run_ids and quality.total_runs must match the ordered runs.

Each association selects exactly `manifest.runs[run_id].output_artifacts[index]`.
Project, run_id and artifact_index are required; task_id, output_id and
source_association_id are included when available. If an artifact contains one of
those optional identities, its association must contain the exact same value.
Output identity comes from the source record, never from a filename. Host
verification supplies the object metadata when source records omit it; conflicting
source digest, object ID, size or media type is rejected. The binding name is
stable across transport and materialization. A binding has one object identity and
one transport filename, with multiple source associations for repeated use. Merge
shared references into that binding; duplicate names, object IDs, digests,
case-insensitive filenames and artifact references are rejected. Each object ID
must equal `"sha256:" + normalized_sha256` exactly. Media digests normalize to
64 lowercase hexadecimal characters; opaque, uppercase or mismatched CAS object
IDs are rejected.

Required references are artifacts with image/audio/video/model_3d kind or a media
MIME family (image/audio/video/model), using media_type or mime_type. Every such
reference needs a binding, even if its source locator is unusable or absent. A
missing required binding, a preexisting resolution error, or a missing host
mapping fails before assembly; it cannot silently become a text card. Bindings
select only these render media references. Nonmedia records remain present even
when they carry object IDs, digests or resolution diagnostics; those identities
alone do not make them render media. Descriptive cards and honest M09 fallback
diagnostics remain possible for nonmedia. Runs with missing outputs and their
quality diagnostics remain present.

The producer calls freeze_inputs to recursively remove locator fields (`path`,
`file`, `file_path`, `local_path`, `locator`, `out_path`, `source_path`,
`storage_path`, `uri`, `url`, `storage_uri`, `download_url`, `prepare_dir`,
`repo_root`). These fields are never used to select media. Other JSON content and
array order remain intact. Frozen consumers reject locator fields rather than
interpreting them. Binding and association tables normalize by name and
(run_id, artifact_index); run arrays retain the producer's deterministic order.

Canonical serialization uses UTF-8, sorted object keys, compact separators,
finite JSON numbers and unescaped Unicode. Authenticated Runtime CAS/input
identity supplies envelope/document integrity and replay identity. The envelope
does not carry documents_sha256 or contract_sha256 and does not claim to
authenticate itself. Media bindings retain their byte SHA-256 digests.
serialize_frozen_inputs/parse_frozen_inputs round-trip this representation and
reject duplicate JSON fields, unknown envelope/binding/association fields,
nonfinite values and unsupported versions. Source mappings are never mutated.

## Transport limits and parser safety

The Runtime transport hard ceiling is 64 MiB per object. This helper validates
declared media object sizes and actual canonical content-document/envelope bytes
against that ceiling. F05 later verifies actual media bytes and enforces the exact
persisted admitted Runtime policy. The helper imposes no semantic count limits
on runs, objects, associations or document records. A nesting depth of 64 is an
implementation recursion/parser safety safeguard, never a product selection or
discovery limit. JSON decoding recursion failures are also rejected.
Names and MIME types are bounded strings. Transport filenames follow Runtime's
safe flat basename contract: at most 512 characters, without path separators,
ASCII controls (including NUL), DEL, or exact `.`/`..` traversal names. Unicode,
spaces, hidden prefixes, Windows reserved-name spellings and internal `..` text
are accepted; the limit counts characters, not UTF-8 bytes.

Discovery metadata, local retained copies and future Rendering-child transport
have separate budgets. The frozen envelope has no child_dependency_bytes or
generated_document_bytes reservations, and this helper does not combine envelope
bytes with media or future child documents. B01/F05's persisted discovery grant
owns the aggregate discovery-record and metadata-byte budgets. F05 counts and
measures the exact Rendering child closure (documents, theme when used, selected
media) against the actual admitted Runtime child input/count/byte policy before
invocation. This helper grants no authority and truncates no required closure.

## Offline assembly and render handoff

1. Trusted host admits the frozen envelope and all binding objects, verifies
   source associations/digests/sizes/media types, and materializes the exact
   binding set using F05's confined map.
2. Pass that map out of band as `Mapping[str, Mapping[str, Any]]`. Each ordinary
   host-owned mapping carries exactly path, object_id, sha256, size, media_type
   and filename. Python mapping or class identity does not prove custody;
   F05's verified confined materialization is the trust boundary. These mappings
   are never decoded from the frozen JSON action input. Paths must be absolute
   staging paths; this helper checks syntax and exact metadata but does not open, resolve, stat,
   hash or establish confinement for them. The host is responsible for the bytes.
3. Call `assembly_inputs(frozen, materialized, project=..., target_run_id=...)`. It
   checks the exact binding map and metadata before returning independent
   input_manifest/input_quality mappings with local artifact paths, plus
   runtime_client=None and runtime_project=None. Pass these kwargs to
   `assemble.assemble_iteration(out_path=host_output_dir,
   repo_root=host_staging_dir, **kwargs)`. The helper does not call assembly or
   import its module. Local paths exist only in this temporary assembly view.
4. M09's produced registry contains file locators. Convert it in memory with
   pathless_render_registry. It maps exact asset_<run_id>_<artifact_index> IDs to
   binding/object/digest/size/media_type, preserves descriptive asset properties,
   removes locators and rejects extra, missing or mismatched assets. Its output
   authorizes no reads. Final transported manifest/quality content must use the
   frozen pathless documents; do not forward the temporary assembly view.
5. The staged consumer supplies timeline and pathless registry as producer files,
   theme only when separately supplied, and one explicit media_dependency file to
   public rendering.render. Host admission still owns authenticated frozen input
   custody, the current parent attempt, lifecycle checks, exact child capability
   and finite input closure. The accepted F05 host path already wires private
   preparation/submission adapters into root execution: it rechecks the admitted
   parent, verifies/stages historical media, uploads the exact Rendering closure
   under current-attempt custody and settles/materializes the child. The new
   pre-admission helper feeds that path. Whole-pack declaration cutover and the
   real selected-media witness remain separate gates.

Frozen helper fixtures remain in tests/packs/video_editing/test_iteration_inputs.py;
focused consumer fixtures are in test_iteration_video_consumer.py. Pre-admission
fixtures are in test_iteration_video_admission.py. The existing eleven-case
test_iteration_video_runtime_integration.py enters through the production helper
and ordinary SDK admission, with explicit boundary faults for downstream rejection
cases. Focused guarded proof requires the accepted generated-client bounded-reader
candidate and the explicitly selected audited D18 Runtime source. It uses fixture
declarations and finite synthetic bytes; no public manifest is changed.
