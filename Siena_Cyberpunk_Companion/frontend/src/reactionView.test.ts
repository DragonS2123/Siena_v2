import assert from 'node:assert/strict'
import test from 'node:test'
import {lifecycleCanReplace,selectLiveReaction} from './reactionView.ts'
import type {GameEvent,ReactionGenerationStatus,SienaReaction} from './types.ts'

const NOW=Date.parse('2026-07-15T12:00:10Z')

function reaction(id:string,priority:SienaReaction['priority'],createdAt:string,sessionId='current'):SienaReaction{
  return {reaction_id:id,event_id:`event-${id}`,session_id:sessionId,event_type:'health_low',text:'Здоровье критическое. Найди укрытие.',created_at:createdAt,priority,provider:'siena_core',requested_provider:'siena_core',fallback_used:false,fallback_reason:null,latency_ms:1000,model:'qwen3.5:9b',request_id:id,metadata:{}}
}

function lifecycle(state:ReactionGenerationStatus['state'],sessionId='current'):ReactionGenerationStatus{
  return {request_id:'request-new',event_id:'event-new',session_id:sessionId,event_type:'health_low',state}
}

const lowEvent={event_id:'event-new',session_id:'current',event_type:'health_low',priority:'P3_LOW',severity:'low'} as GameEvent
const criticalEvent={...lowEvent,priority:'P0_CRITICAL',severity:'critical'} as GameEvent

test('critical replaces a low live reaction',()=>{
  const low=reaction('low','low','2026-07-15T12:00:08Z')
  const critical=reaction('critical','critical','2026-07-15T12:00:09Z')
  assert.equal(selectLiveReaction([critical,low],'current',NOW)?.reaction_id,'critical')
})

test('low does not replace a fresh critical during the hold',()=>{
  const low=reaction('low','low','2026-07-15T12:00:09Z')
  const critical=reaction('critical','critical','2026-07-15T12:00:04Z')
  assert.equal(selectLiveReaction([low,critical],'current',NOW)?.reaction_id,'critical')
  assert.equal(lifecycleCanReplace(critical,lifecycle('queued'),[lowEvent],NOW),false)
  assert.equal(lifecycleCanReplace(critical,lifecycle('queued'),[criticalEvent],NOW),true)
})

test('suppressed and old-session lifecycle never replace the card',()=>{
  const current=reaction('current','medium','2026-07-15T12:00:09Z')
  assert.equal(lifecycleCanReplace(current,lifecycle('suppressed'),[criticalEvent],NOW),false)
  assert.equal(selectLiveReaction([reaction('old','critical','2026-07-15T12:00:09Z','old')],'current',NOW),null)
})

test('REST bootstrap data restores latest current-session Unicode reaction',()=>{
  const latest=reaction('latest','high','2026-07-15T12:00:09Z')
  const older=reaction('older','low','2026-07-15T12:00:01Z')
  assert.equal(selectLiveReaction([latest,older],'current',NOW)?.text,'Здоровье критическое. Найди укрытие.')
})
