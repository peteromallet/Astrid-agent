# wan2gp.generate_video — Stage

Host-mediated native Wan2GP video generation. The action declaration exposes
the stable `wan2gp.generate_video` capability and its typed command mapping;
GenericPackHost selects the exact capability ID's admitted Wan session before
generic command or worker fallback.

The relocated runner is a direct-run guard. It compiles the existing typed
inputs, then fails closed with `host_session_required`; it does not initialize
or close a native session per attempt. Native output custody, cancellation,
settlement, and release remain host/Runtime responsibilities.

Use the public SDK with an explicit project and admitted host session. This
pack declaration does not qualify a real Wan2GP checkout, model, provider,
network, GPU, deployment, model residency, or warm reuse.

Declared outputs are the host-harvested `generated_videos` file result and the
universal `video_manifest` control file. The host may publish the listed video
result while retaining the manifest as custody evidence.
