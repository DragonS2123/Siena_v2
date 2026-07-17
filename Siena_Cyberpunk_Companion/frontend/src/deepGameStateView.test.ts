import test from 'node:test'
import assert from 'node:assert/strict'
import {cyberdeckSlotNumber,formatBuildAwareness,formatCyberdeck,formatResource,shortenWeaponRecord} from './deepGameStateView.ts'

test('deep resources handle valid and unavailable values',()=>{
  assert.deepEqual(formatResource(25,100),{text:'25.0 / 100.0 (25%)',percent:25})
  assert.deepEqual(formatResource(null,100),{text:'—',percent:null})
  assert.deepEqual(formatResource(10,0),{text:'—',percent:null})
})

test('weapon record uses stable readable identity and rejects userdata',()=>{
  assert.equal(shortenWeaponRecord('Items.Preset_Sidewinder_Default'),'Preset_Sidewinder_Default')
  assert.equal(shortenWeaponRecord('ToTweakDBID{ --[[ Items.Preset_Ajax_Default --]] }'),'Preset_Ajax_Default')
  assert.equal(shortenWeaponRecord('userdata: 0x1234'),'—')
  assert.equal(shortenWeaponRecord(null),'—')
})

test('cyberdeck view handles old payloads, no deck and partial capacity',()=>{
  assert.equal(formatCyberdeck(undefined,undefined).identity,'—')
  assert.equal(formatCyberdeck(null,true).identity,'No cyberdeck')
  const view=formatCyberdeck({record_id:'Items.AdvancedNetwatchNetdriverMKLegendary',quality:'Legendary',iconic:true,program_capacity:{used:1,total:null},programs:[{slot_id:'AttachmentSlots.CyberdeckProgram2',record_id:'Items.PingLvl4Program'}],truncated:false},true)
  assert.equal(view.identity,'AdvancedNetwatchNetdriverMKLegendary · ICONIC')
  assert.equal(view.capacity,'1 / ?')
  assert.deepEqual(view.programs,[{slot:'2',record:'PingLvl4Program'}])
})

test('cyberdeck program rendering stays bounded and validates slot labels',()=>{
  const programs=Array.from({length:10},(_,index)=>({slot_id:`AttachmentSlots.CyberdeckProgram${index%8+1}`,record_id:`Items.Program${index}`}))
  assert.equal(formatCyberdeck({record_id:'Items.Deck',quality:null,iconic:false,program_capacity:null,programs,truncated:true},true).programs.length,8)
  assert.equal(cyberdeckSlotNumber('AttachmentSlots.GenericItemRoot'),'—')
})

test('build awareness renders null, unknown categories and bounded evidence safely',()=>{
  assert.equal(formatBuildAwareness(null).style,'—')
  const view=formatBuildAwareness({
    profile_version:'0.8.5',operating_system:null,resources:null,current_weapon_record_id:null,
    summary_key:'cyberdeck_unknown',confidence:.55,
    quickhack_loadout:{installed:1,capacity:8,fill_percent:12.5,known_programs:0,unknown_programs:1,categories:{offensive:0,control:0,recon:0,utility:0,unknown:1},dominant_category:null,style:'cyberdeck_unknown',confidence:.55},
    evidence:['one','two','three','four','five'],limitations:['unknown_quickhack_records'],
  })
  assert.equal(view.style,'cyberdeck unknown')
  assert.equal(view.confidence,'55%')
  assert.equal(view.knownUnknown,'0 / 1')
  assert.equal(view.counts,'unknown 1')
  assert.equal(view.evidence.length,4)
})
