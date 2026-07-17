# Siena Embodied Companion v0.10.0

The CET state machine owns the single `Siena.SienaCompanion` body, local hotkeys,
bounded stuck detection, speech look-at lifecycle and sanitized screen-space
subtitles. Presence/auto-spawn and rescue default off; rescue cannot be enabled
until a Siena-only native teleport path is live validated. `siena_default` is a
stable appearance contract. Its Stage A binary WolvenKit resources remain an
authoring blocker until real CR2W `.ent/.app` files and the archive are built.

Production spawning is fixed to `Siena.SienaCompanion`. The generic
`Character.CitizenRichFemaleCasual` appears only in research evidence and as the
TweakXL `$base`; it is never the controller's spawn record. Record IDs, positions,
EntityIDs, class/method names and animation names cannot come from HTTP or an LLM.

The fixed semantic allowlist covers player, deterministic system and bounded
speech lifecycle commands. The controller reuses RedHttpClient with one request
in flight and bounded backoff. To activate after guarded asset deployment, set the
installed controller `enabled = true` and backend
`SIENA_CP_NPC_CONTROLLER_ENABLED=true`. Presence and auto-spawn remain separately
controlled settings; rescue stays disabled pending live validation.
