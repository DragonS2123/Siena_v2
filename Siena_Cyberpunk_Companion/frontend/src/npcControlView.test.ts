import assert from 'node:assert/strict'
import test from 'node:test'
import {EMBODIMENT_TOGGLES,NPC_COMMANDS,npcControlsDisabled,npcErrorText,npcValue,runtimeAppearanceText,temporaryBodyWarning} from './npcControlView.ts'
test('manual NPC buttons expose only the deterministic allowlist',()=>assert.deepEqual(NPC_COMMANDS.map(item=>item.command),['spawn','despawn','follow','stay','come_here','look_at_player','clear_look_at']))
test('null status renders honestly',()=>assert.equal(npcValue(null),'—'))
test('temporary appearance warning is explicit',()=>{assert.match(temporaryBodyWarning(null),/not connected/);assert.match(temporaryBodyWarning({temporary_appearance:true,appearance:'siena_default',entity_record:'Siena.SienaCompanion'} as never),/siena_default.*Siena\.SienaCompanion/)})
test('standalone runtime appearance reports exact value and null honestly',()=>{assert.equal(runtimeAppearanceText(null),'—');assert.equal(runtimeAppearanceText({current_appearance:null} as never),'—');assert.equal(runtimeAppearanceText({current_appearance:'siena_default'} as never),'siena_default')})
test('all required runtime toggles are exposed and rescue remains explicit',()=>{assert.equal(EMBODIMENT_TOGGLES.length,8);assert.equal(EMBODIMENT_TOGGLES.at(-1)?.[0],'npc_rescue_enabled')})
test('loading and disabled status block commands',()=>{assert.equal(npcControlsDisabled(true,{enabled:true} as never),true);assert.equal(npcControlsDisabled(false,null),true);assert.equal(npcControlsDisabled(false,{enabled:true} as never),false)})
test('local and controller errors render deterministically',()=>{assert.equal(npcErrorText('request failed',null),'request failed');assert.equal(npcErrorText('',{last_error:'runtime failed'} as never),'runtime failed')})
