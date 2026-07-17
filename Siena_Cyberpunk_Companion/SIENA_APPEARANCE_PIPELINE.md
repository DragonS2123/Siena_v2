# Siena Appearance Pipeline

Runtime identity is permanently `Siena.SienaCompanion`; runtime Lua never accepts
a record ID and never clones TweakDB records. The TweakXL source is
`assets/siena_npc/source/r6/tweaks/siena_npc.yaml`. `asset-manifest.json` fixes the
appearance name `siena_default` and expected `.ent`, `.app`, ArchiveXL, and archive
paths.

The live-confirmed Stage A inputs are
`ep1/characters/entities/citizen/citizen__ep1_rich_wa.ent`,
`base/characters/appearances/citizen/citizen__rich_wa.app`, and appearance entry
entity display label `citizen__rich_wa_rich_23_casual`, and actual mapped
appearance definition `rich_23_casual`. Their Siena-owned outputs are
`siena/entities/siena_default.ent`, `siena/appearances/siena_default.app`, and
entry `siena_default`.

The native WolvenKit project is `wolvenkit/siena_npc/siena_npc.cpmodproj` and
retains WolvenKit's generated `source/archive`, `source/raw`, and
`source/resources` layout. Its TweakXL source lives under
`source/resources/r6/tweaks`; the deployment copy under `assets` is validator-
checked byte-for-byte against it.

Stages: A copies the proven non-quest female citizen entity/appearance into the
Siena namespace; B introduces a
female-V-compatible human rig; C replaces head, hair, body and clothing mesh slots
with Siena resources; D supplies final Blender-authored meshes. Each mesh must use
the same female human NPC skeleton/weights, compatible material instances and
package-owned `siena/...` texture paths. Do not attach quest NPC components.

The local audit found WolvenKit GUI 8.19.0 at
`C:\Users\frunk\AppData\Local\Programs\WolvenKit\WolvenKit.exe`, but no separate
CLI executable suitable for a headless **Build Project** equivalent. WolvenKit
must author `siena/entities/siena_default.ent` and
`siena/appearances/siena_default.app`, then produce exactly one detected
`siena_npc.archive` below `wolvenkit/siena_npc/packed`. Stage A needs no
ArchiveXL mapping; `siena_npc.archive.xl` is optional and must exist only when a
later asset stage introduces a real ArchiveXL rule. Binaries are never fabricated.
The guarded deployment has no build operation and refuses missing/non-CR2W source
files or a missing archive. The controller also checks
`TweakDB:GetRecord(TweakDBID.new("Siena.SienaCompanion"))` before spawn and
fails closed when the standalone record is unavailable. TweakXL installs under `r6/tweaks/siena_npc`; the
archive installs under `archive/pc/mod`. Rollback restores the exact pre-deployment
Siena paths from a timestamped backup outside active mod directories.

Validation checks namespace, paths, appearance name, safe base record, and absence
of PlayerPuppet or named quest dependencies. Actual mesh, rig, material, texture,
`.app`, `.ent`, rig/dependency and packed-archive validation remains blocked until
WolvenKit produces real resources. Exact base-resource resolution and UI steps are
in `wolvenkit/siena_npc/SIENA_BASE_ASSET_MANIFEST.md` and
`wolvenkit/siena_npc/WOLVENKIT_AUTHORING_CHECKLIST.md`.
