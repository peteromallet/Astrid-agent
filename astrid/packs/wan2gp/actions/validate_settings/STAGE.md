# wan2gp.validate_settings — Stage

Offline Wan2GP settings validation. The action compiles the admitted portable
inputs through `src/compiler.py`, validates the resulting settings through the
existing driver contract, and returns the structured JSON payload containing
`settings`, `portable_digest`, and the disclosed engine pin.

This action has no file outputs and is covered by the existing `no-artifact`
exemption. It does not initialize the native engine, create media, write a
manifest, call a provider, use a network, or require a GPU.
