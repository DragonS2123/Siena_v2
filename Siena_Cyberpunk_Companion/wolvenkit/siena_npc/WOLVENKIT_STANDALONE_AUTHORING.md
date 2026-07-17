# Standalone Siena Authoring — WolvenKit 8.19.0

Confirmed sources already present in Project Explorer:

- `source\archive\ep1\characters\entities\citizen\citizen__ep1_rich_wa.ent`
- `source\archive\base\characters\appearances\citizen\citizen__rich_wa.app`
- entity entry display label `citizen__rich_wa_rich_23_casual`
- actual `appearanceName` / `.app` definition name `rich_23_casual`

Targets are fixed:

- `source\archive\siena\entities\siena_default.ent`
- `source\archive\siena\appearances\siena_default.app`
- appearance name `siena_default`

## Automated coverage

`CreateSienaStandaloneAssets.wscript` uses only APIs present in bundled 8.19.0
scripts: `GetProjectFiles`, `FileExistsInProject`, `GetFileFromProject` with
`OpenAs.GameFile`, `GameFileToJson`, `JsonToCR2W`, `SaveToProject`, and
project re-read through `GetFileFromProject`. It copies
only the two project CR2W resources, changes exact matching appearance values,
retains every unrelated definition/reference, verifies root types and saved
references, and never writes to game archives.

Install it with `scripts\install_siena_wscript.ps1`, restart WolvenKit, open the
Siena project, then run `CreateSienaStandaloneAssets` from the WScript list. Leave
`SAFE_REPLACE = false` for the first run. Setting it true is an explicit replace
decision and creates raw JSON backups for pre-existing destinations.

The WScript has **not** been executed by repository preparation.

## Manual fallback — exact Project Explorer operation

Use this only if the WScript reports that the binary structure cannot be safely
converted.

1. Open `siena_npc.cpmodproj` with **Open Project**.
2. In Project Explorer, expand `source > archive > ep1 > characters > entities >
   citizen`. Right-click `citizen__ep1_rich_wa.ent` and choose **Copy**.
3. Under `source > archive`, create folders `siena > entities`. Right-click
   `entities`, choose **Paste**, then rename the pasted file to
   `siena_default.ent`. In the rename dialog enable **Update in project files** so
   references within project CR2W resources follow the move. The original ep1 file
   must remain at its original path.
4. Expand `source > archive > base > characters > appearances > citizen`.
   Right-click `citizen__rich_wa.app`, choose **Copy**.
5. Create `source > archive > siena > appearances`, paste there, rename to
   `siena_default.app`, and enable **Update in project files**. The original base
   file must remain unchanged.

If Copy/Paste is unavailable for an archive node, use **Add to Project** on the
already confirmed source, then duplicate the project file from Project Explorer;
do not copy bytes into a text editor and do not create an empty extension file.

## Manual `.app` edit

1. Open `siena\appearances\siena_default.app` in the CR2W editor.
2. Confirm `Data > RootChunk` type is `appearanceAppearanceResource`.
3. Expand `RootChunk > appearances` and select the
   `appearanceAppearanceDefinition` whose `name` is exactly `rich_23_casual`.
   Do not search for the composite entity display label in the `.app`.
4. Change only that definition's `name` to `siena_default`. Do not delete other
   appearance definitions in Stage A.
5. Leave its parts/components, mesh paths, material paths, rig/deformation paths
   and animation dependencies unchanged. Every depot reference must still resolve.

Expected result: the Siena-owned `.app` contains one definition named
`siena_default` with the same part/dependency graph as the confirmed casual source.
Warning signs are a second `siena_default`, missing parts, a named quest resource,
PlayerPuppet resource, or unresolved red reference.

## Manual `.ent` edit

1. Open `siena\entities\siena_default.ent` and confirm `Data > RootChunk` type is
   `entEntityTemplate`.
2. Expand `RootChunk > appearances`. Select the `entTemplateAppearance` entry
   displayed as `citizen__rich_wa_rich_23_casual`. Verify its actual
   `appearanceName` is `rich_23_casual` and its `appearanceResource` is exactly
   `base\characters\appearances\citizen\citizen__rich_wa.app`.
3. Set that entry's `appearanceName` to `siena_default`.
4. Set that same entry's `appearanceResource` depot path to exactly
   `siena\appearances\siena_default.app`.
5. If the editor exposes an existing default/selected appearance field on the
   template, change its exact old value
   `rich_23_casual` to `siena_default`. Do not invent a field if
   the template has none; TweakXL `appearanceName` is the stable selector.
6. Inspect all components. Preserve the source AI/controller, animated/rig,
   targeting/senses and reaction components. Do not add PlayerPuppet, quest or
   named-character components.

Expected result: the Siena entity retains the source NPC component/rig graph, has
an exact `siena_default` entry, and that entry points only to the Siena-owned app.
The source ep1 entity must remain byte-identical.

## Save, validate and visually confirm

1. Choose **Save All**. Close and reopen both Siena resources.
2. Re-check root types and the exact fields above.
3. Run WolvenKit **File Validation** on each Siena resource. Resolve all broken
   project/depot references before packing.
4. Visually compare the source and Siena definitions in the property editor:
   meshes, materials, rig, animation and components must match except the intended
   path/name substitutions.
5. Confirm `source/resources/r6/tweaks/siena_npc.yaml` selects
   `siena\entities\siena_default.ent` and `siena_default`.
6. Use WolvenKit 8.19's **Pack Project** action with install and game launch off.
   Record the actual generated archive path; do not relocate or rename an unknown
   output without updating the asset manifest.

## Rollback

Before a replace run, keep the WScript raw backups under
`source/raw/siena_authoring_backups`. To abandon new targets, close their editor
tabs and delete only `source/archive/siena/entities/siena_default.ent` and
`source/archive/siena/appearances/siena_default.app`; the original ep1/base files
remain untouched. After deployment, use the printed timestamped backup with
`uninstall_siena_embodied_companion.ps1 -BackupPath <path>`.

Visual acceptance of the copied citizen appearance is manual. It does not prove a
final custom Siena model; Blender head/hair/body/clothing replacement remains a
later asset stage.
