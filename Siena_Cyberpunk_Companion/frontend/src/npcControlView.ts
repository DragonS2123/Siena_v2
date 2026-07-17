import type {NpcCommandName,NpcStatus} from './types'
export const NPC_COMMANDS:ReadonlyArray<{command:NpcCommandName;label:string}>=[{command:'spawn',label:'Spawn'},{command:'despawn',label:'Despawn'},{command:'follow',label:'Follow'},{command:'stay',label:'Stay'},{command:'come_here',label:'Come Here'},{command:'look_at_player',label:'Look At'},{command:'clear_look_at',label:'Clear Look At'}]
export const npcValue=(value:boolean|string|null|undefined)=>value==null?'—':typeof value==='boolean'?(value?'YES':'NO'):value
export const runtimeAppearanceText=(status:NpcStatus|null)=>status?.current_appearance??'—'
export const temporaryBodyWarning=(status:NpcStatus|null)=>status?.temporary_appearance?`Temporary appearance: ${status.appearance}; standalone record: ${status.entity_record}`:status?'Packaged Siena appearance active.':'Siena appearance is not connected.'
export const EMBODIMENT_TOGGLES=[['npc_presence_enabled','Presence'],['npc_auto_spawn','Auto-spawn'],['npc_auto_follow','Auto-follow'],['npc_suspend_in_vehicle','Vehicle suspension'],['npc_suspend_during_combat','Combat suspension'],['npc_voice_embodiment_enabled','Voice embodiment'],['npc_in_game_subtitles_enabled','In-game subtitles'],['npc_rescue_enabled','Rescue (unvalidated)']] as const
export const npcControlsDisabled=(busy:boolean,status:NpcStatus|null)=>busy||status?.enabled!==true
export const npcErrorText=(localError:string,status:NpcStatus|null)=>localError||status?.last_error||''
