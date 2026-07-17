# Siena Embodiment Safety Model

The language model supplies only reaction wording through the existing Siena Core
path. Deterministic policy decides whether speech is allowed. Playback emits a
bounded utterance ID, sanitized subtitle, urgency, capped duration, and one local
semantic gesture category. CET maps unsupported gestures to look-at plus idle;
there are no arbitrary animation names or method dispatch.

Only the state machine calls the entity adapter. Allowed writes are create/delete
the tagged Siena entity, native follow/cancel/move-near, temporary look-at, and UI
draw. Rescue remains hard-disabled pending live evidence. Entity persistence flags
are false and the tag/managed lookup prevents duplicates.
Before creation, the adapter performs a read-only lookup of the fixed
`Siena.SienaCompanion` record and rejects spawn if it is absent. After resolution,
it reads the current appearance when that CET API is available and rejects a
readable value other than `siena_default`. No runtime appearance override field
is guessed or written.

Forbidden surfaces are player movement/input/teleport, combat/weapon/quickhack,
target or world scans, inventory/health/money/quest/faction writes, runtime TweakDB
mutation, arbitrary records/methods/animations, extra network clients, and direct
LLM queue access. Session loss and shutdown cancel movement/look-at/subtitles and
delete only the managed Siena entity.
