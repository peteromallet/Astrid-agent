# Publish a verified H3 candidate

Consumes the managed `verified_candidate` output from `h3_av.verify`.
Copies those exact bytes into the attempt output spool and emits a result
manifest with one video output. It performs no inference or composition.

The delegated parent policy binds this input to the verify task and association.
Runtime owns the final generation publication effect and settlement. A successful
local copy alone is not proof that publication settled.

The direct transform's legacy `candidate` alias retains its final-composition
selector; the delegated policy admits only `verified_candidate`.
