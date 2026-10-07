# Personal Seedance fal.ai adapter

This checkout-local pack exposes Seedance 2.0 reference-to-video as a proper
Astrid executor while keeping it out of Astrid core.

The executor validates fal.ai's current reference-video limits, stages the
exact prompt and reference clip, submits one bounded request, downloads the
result, and writes a universal manifest that the experiment layer can import.

The prompt must refer to the staged clip as `@Video1`.
