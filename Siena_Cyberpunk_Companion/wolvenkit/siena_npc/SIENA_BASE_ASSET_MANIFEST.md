# Siena Base Asset Manifest — WolvenKit 8.19.0

This manifest separates the now-confirmed source identity from visual and CR2W
details that still require WolvenKit's property editor. Pending details are
acceptance checks, not guessed path placeholders.

## Confirmed source identity

| Property | Value | Evidence |
|---|---|---|
| TweakDB record | `Character.CitizenRichFemaleCasual` | Production v0.9.1 config and live log |
| Runtime puppet class | `NPCPuppet` | `siena_npc_controller.log`: `npc_spawn_succeeded runtime_class=NPCPuppet` |
| Dynamic entity compatibility | Confirmed | v0.9.1 live spawn: managed/spawned/resolved |
| AI controller | Present | v0.9.1 live validation |
| Component | `ReactionManager` present | v0.9.1 live validation |
| Non-quest selection | Generic citizen record; no named quest record selected | Production/research config |

Local evidence is searchable at:

- `B:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\siena_npc_controller\siena_npc_controller.log`
- `B:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\AppearanceMenuMod\db.sqlite3`
- `B:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\AppearanceMenuMod\init.lua`
- `B:\SteamLibrary\steamapps\common\Cyberpunk 2077\r6\cache\tweakdb.bin`

## Exact base-resource resolution

| Required value | Current value | WolvenKit search/action | Acceptance evidence |
|---|---|---|---|
| `entityTemplatePath` | `ep1\characters\entities\citizen\citizen__ep1_rich_wa.ent` | Confirmed Tweak Browser export and physical project CR2W | Source exists, header `CR2W`, SHA-256 `8EECF5884FD0AAB60AA652953250F68ECD0A77C26E38307DD6D072ED34B60E56` |
| Entity appearance entry display | `citizen__rich_wa_rich_23_casual` | Manually verified in WolvenKit | Composite display label; not the `.app` definition name |
| Actual `appearanceName` and `.app` definition | `rich_23_casual` | Live WolvenKit 8.19 CR2W editor and serialized entity mapping | Must be derived from the exact entity entry and match uniquely in the `.app` |
| Appearance `.app` resource | `base\characters\appearances\citizen\citizen__rich_wa.app` | Manually verified and physical project CR2W | Source exists, header `CR2W`, SHA-256 `4C437D28462DAC23BF8863EEA0501CD2FD9FDC593477166A14981FDBB6AC249A` |
| Puppet/entity type in CR2W | **PENDING GUI VALIDATION**; runtime is `NPCPuppet` | Asset Browser: open `.ent`, inspect root entity type | Root type supports NPC puppet components |
| Rig/skeleton depot path | **PENDING GUI INSPECTION** | In confirmed `.ent` and `.app`, inspect animated/rig components and rig resource paths | Every referenced rig path resolves |
| Required mesh/material dependencies | **PENDING GUI INSPECTION** | Asset Browser dependency view after adding `.ent` and `.app` | All red references resolve; no missing resources |
| Required controller components | Names unresolved; runtime AI controller and `ReactionManager` confirmed | Inspect `.ent` components | AI/controller and reaction/look-at support remain present |

The removed Tweak Browser export also confirmed `characterType: NPCType.Human`,
`affiliation: Factions.Civilian`, `reactionPreset: ReactionPresets.Civilian_Neutral`,
`crowdMemberSettings: Crowds.WomanCivilianSettings`, and `savable: False`. It was
evidence only and was removed from `source/resources` so the mod cannot redefine
the original record.

AMM associates the record hash for `Character.CitizenRichFemaleCasual` with
`citizen__rich_wa_rich_01` through `citizen__rich_wa_rich_22`, and
`citizen__rich_wa_rich_23_casual` through
`citizen__rich_wa_rich_30_casual`. This is a candidate set, **not proof of the
default appearance**. AMM labels the related `Character.CitizenRichFemale` record
with rig `woman_base`; that is supporting evidence only and is **not an exact rig
depot path for the Casual record**.

## Siena-owned destinations

| Resource | Depot path | Project path |
|---|---|---|
| Entity | `siena\entities\siena_default.ent` | `source\archive\siena\entities\siena_default.ent` |
| Appearance | `siena\appearances\siena_default.app` | `source\archive\siena\appearances\siena_default.app` |
| TweakXL | `r6\tweaks\siena_npc\siena_npc.yaml` | `source\resources\r6\tweaks\siena_npc.yaml` |
| Built archive | deployment: `archive\pc\mod\siena_npc.archive` | exactly one detected `siena_npc.archive` below project `packed` |

The production record inherits safe gameplay defaults from the generic citizen but
overrides `entityTemplatePath` with the Siena-owned entity and sets
`appearanceName: siena_default`. It never modifies the original record.
