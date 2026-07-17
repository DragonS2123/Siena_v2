# WolvenKit 8.19.0 Authoring Checklist

The source entity, appearance resource, and appearance entry are live-confirmed.
The destination paths below are fixed. Do not substitute another citizen or a
named quest NPC.

## 1. Open and resolve the base record

| UI operation | Exact input/destination | Expected field/result | Warning signs |
|---|---|---|---|
| WolvenKit **Open Project** | `G:\Siena_v2\Siena_Cyberpunk_Companion\wolvenkit\siena_npc\siena_npc.cpmodproj` | Project tree shows `source/archive`, `source/raw`, `source/resources` | A new empty project or another project name is open |
| Open **Tweak Browser** and search exact record | `Character.CitizenRichFemaleCasual` | Exactly that record opens | A named NPC, PlayerPuppet, or similarly named record is selected |
| Copy the full value | `entityTemplatePath` | Complete lowercase depot path ending `.ent`; enter it in the base manifest and WScript | Blank/hash-only display, truncated path, or inferred filename |
| Copy the default appearance | Record `appearanceName`, or the resolved entity's explicit default selector if record field is absent | Exact CName copied to manifest/WScript | Selecting the first AMM appearance candidate without evidence |

## 2. Add and inspect the base entity

| UI operation | Exact input/destination | Expected field/result | Warning signs |
|---|---|---|---|
| In **Asset Browser**, paste the confirmed entity path | `ep1\characters\entities\citizen\citizen__ep1_rich_wa.ent` | One `.ent` result at the exact depot path | No result, quest/player path, different gender/body family |
| Open the archive file before adding | Exact result above | CR2W opens without parse errors | Broken resource or missing archive |
| Use **Add to Project** | Initially preserve its original depot path | Entity appears under `source/archive/<base path>` | Adding an entire quest dependency tree |
| Inspect appearance reference(s) | Resource-reference field in the entity whose target ends `.app` | Exact `.app` depot path copied to base manifest | Guessing from appearance name alone |
| Inspect root/components | All component entries | NPC controller/AI-related components and `ReactionManager` support are retained | Player-specific components, quest controllers, missing AI/reaction support |
| Inspect rig/animation references | Animated/rig components and every referenced rig/deformation resource | Exact depot paths recorded; all resolve in Asset Browser | `PlayerPuppet`, missing rig, incompatible male/creature skeleton |

Only the entity and its appearance resource are Siena-owned copies in Stage A.
Meshes, materials, rigs and animations that remain unchanged should keep valid
base-game references; do not duplicate the entire dependency graph.

## 3. Add and inspect the base appearance

| UI operation | Exact input/destination | Expected field/result | Warning signs |
|---|---|---|---|
| Asset Browser search | `base\characters\appearances\citizen\citizen__rich_wa.app` | Exact `.app` resource opens | Using an `.app` from a named quest NPC |
| **Add to Project** | Preserve original depot path temporarily | `.app` appears under `source/archive/<base path>` | Missing file or unrelated body family |
| Find the confirmed default entry | Exact actual definition name `rich_23_casual` | One matching appearance definition | Searching for the composite entity display label or accepting multiple matches |
| Inspect parts/components | Mesh/component entries referenced by that definition | Female human rig-compatible parts and resolvable materials/textures | Missing paths, player-only component, quest-specific head/body dependency |

## 4. Create Siena-owned resources

Preferred automated path: review and run
`scripts/CreateSienaStandaloneAssets.wscript`. Its confirmed `BASE_*` constants
are already filled in. Use `scripts/install_siena_wscript.ps1` to copy it to
`%APPDATA%\REDModding\WolvenKit\WScript`; the helper requires WolvenKit to be
closed, backs up an existing same-name script, and verifies the copied hash.
After use, remove it with `scripts/uninstall_siena_wscript.ps1`. The WScript
refuses player/quest source paths and existing destination files by default.

Manual equivalent:

| Operation | Source | Exact destination | Required edit/result | Warning signs |
|---|---|---|---|---|
| Duplicate entity | `ep1\characters\entities\citizen\citizen__ep1_rich_wa.ent` | `source\archive\siena\entities\siena_default.ent` | Real CR2W `.ent`, not text | Zero-byte/text file; original archive edited |
| Duplicate appearance | `base\characters\appearances\citizen\citizen__rich_wa.app` | `source\archive\siena\appearances\siena_default.app` | Real CR2W `.app`, not text | Original resource overwritten |
| Rename appearance definition | `rich_23_casual` | `siena_default` inside Siena `.app` | One exact default definition named `siena_default` | Arbitrary global replacement of unrelated names |
| Remap entity appearance resource | Exact old `.app` reference | `siena\appearances\siena_default.app` | Siena `.ent` points to Siena `.app` | Entity still directly points to original citizen `.app` |
| Remap entity default appearance | Exact old CName | `siena_default` | Entity selects the owned default | Name not present in Siena `.app` |

The WScript changes only exact string matches. It deliberately does not guess
component fields, clone all dependencies, or overwrite existing Siena resources.

## 5. Verify the production record

Open `source/resources/r6/tweaks/siena_npc.yaml` and verify exactly:

```yaml
Siena.SienaCompanion:
  $base: Character.CitizenRichFemaleCasual
  entityTemplatePath: siena\entities\siena_default.ent
  appearanceName: siena_default
```

The `$base` supplies non-quest gameplay defaults; the production entity path must
not point to the original citizen `.ent`. Do not edit
`Character.CitizenRichFemaleCasual` itself.

## 6. Validate and build

1. Save all open `.ent`, `.app`, and resource files.
2. Run WolvenKit **File Validation** on `siena_default.ent` and
   `siena_default.app`. Resolve every missing depot reference or CR2W error.
3. Confirm project archive tree contains exactly the two Siena CR2W resources plus
   intentional Siena-owned additions. Base-game dependencies may remain external.
4. Select the build profile with **Install = false** and **Launch Game = false**.
   Do not use the locally configured `Install` profile for this preparation pass.
5. Run **Pack Project** (or the WolvenKit 8.19.0 project action that produces the
   packed mod without installation).
6. Inspect `wolvenkit\siena_npc\packed` and locate the produced
   `siena_npc.archive`. The validators search this directory recursively and
   require exactly one detected result; they do not guess a deeper output path.
7. Run `scripts/validate_siena_wolvenkit_assets.ps1`. It intentionally fails if
   either target CR2W or the packed archive is absent.

Expected deployed files after the later guarded deployment:

- `archive\pc\mod\siena_npc.archive`
- `r6\tweaks\siena_npc\siena_npc.yaml`
- optional `archive\pc\mod\siena_npc.archive.xl` only if later authoring actually
  introduces an ArchiveXL mapping; Stage A does not require it.

## 7. Install, verify, rollback

With the game closed, run the repository's guarded deployment script only after
the readiness checker passes. It backs up all Siena-owned active paths outside the
mod directories. Then run `verify_siena_embodied_companion.ps1` while the v0.10.0
backend is enabled. If asset validation fails in game, run
`uninstall_siena_embodied_companion.ps1` with the backup path printed by deployment
to remove the failed package and restore the prior Siena files.

Never delete or overwrite unrelated archives, tweaks, CET mods, or user settings.
