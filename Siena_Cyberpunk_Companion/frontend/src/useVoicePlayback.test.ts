import test from 'node:test'
import assert from 'node:assert/strict'
import {enqueueVoiceClip,isPlayableClip,leaseCanBeClaimed,shouldInterrupt} from './useVoicePlayback.ts'
import type {VoiceClipReady,VoicePriority} from './types.ts'

const clip=(id:string,priority:VoicePriority='medium',session='session-1',expires='2099-01-01T00:00:00Z'):VoiceClipReady=>({
  voice_clip_id:id,voice_request_id:`request-${id}`,reaction_id:`reaction-${id}`,
  session_id:session,scene_id:'scene-1',scene_revision:1,focus:'combat_comment',priority,
  audio_url:`/api/v1/voice/audio/${id}`,content_type:'audio/wav',duration_ms:1000,
  tts_provider:'fake',speaker:'serena',expires_at:expires,
})

test('critical is queued before low',()=>assert.deepEqual(enqueueVoiceClip([clip('low','low')],clip('critical','critical')).map(item=>item.voice_clip_id),['critical','low']))
test('high is queued before medium',()=>assert.deepEqual(enqueueVoiceClip([clip('medium')],clip('high','high')).map(item=>item.priority),['high','medium']))
test('info remains behind low',()=>assert.deepEqual(enqueueVoiceClip([clip('info','info')],clip('low','low')).map(item=>item.priority),['low','info']))
test('same clip id is not duplicated',()=>assert.equal(enqueueVoiceClip([clip('one')],clip('one')).length,1))
test('same reaction id is not duplicated',()=>{const other={...clip('two'),reaction_id:'reaction-one'};const first={...clip('one'),reaction_id:'reaction-one'};assert.equal(enqueueVoiceClip([first],other).length,1)})
test('playback queue is bounded to ten',()=>{let queue:VoiceClipReady[]=[];for(let i=0;i<12;i++)queue=enqueueVoiceClip(queue,clip(String(i)));assert.equal(queue.length,10)})
test('custom playback queue bound is honored',()=>assert.equal(enqueueVoiceClip([clip('a'),clip('b')],clip('c'),2).length,2))
test('current session unexpired clip is playable',()=>assert.equal(isPlayableClip(clip('one'),'session-1',Date.now()),true))
test('old session clip is not playable',()=>assert.equal(isPlayableClip(clip('one','medium','old'),'session-1',Date.now()),false))
test('expired clip is not playable',()=>assert.equal(isPlayableClip(clip('one','medium','session-1','2020-01-01T00:00:00Z'),'session-1',Date.now()),false))
test('clip is not playable without active session',()=>assert.equal(isPlayableClip(clip('one'),null,Date.now()),false))

for(const priority of ['info','low','medium'] as VoicePriority[]){
  test(`critical interrupts playing ${priority}`,()=>assert.equal(shouldInterrupt(clip('current',priority),clip('critical','critical'),'critical_only'),true))
}
for(const priority of ['high','critical'] as VoicePriority[]){
  test(`critical does not interrupt playing ${priority}`,()=>assert.equal(shouldInterrupt(clip('current',priority),clip('critical','critical'),'critical_only'),false))
}
test('never mode does not interrupt low',()=>assert.equal(shouldInterrupt(clip('current','low'),clip('critical','critical'),'never'),false))
test('non-critical incoming clip never interrupts',()=>assert.equal(shouldInterrupt(clip('current','low'),clip('high','high'),'critical_only'),false))
test('nothing is interrupted when there is no current clip',()=>assert.equal(shouldInterrupt(null,clip('critical','critical'),'critical_only'),false))

test('empty leader lease can be claimed',()=>assert.equal(leaseCanBeClaimed(null,'tab-a',100),true))
test('own leader lease can be renewed',()=>assert.equal(leaseCanBeClaimed({tabId:'tab-a',expiresAt:200},'tab-a',100),true))
test('expired foreign leader lease can be claimed',()=>assert.equal(leaseCanBeClaimed({tabId:'tab-b',expiresAt:99},'tab-a',100),true))
test('active foreign leader lease cannot be claimed',()=>assert.equal(leaseCanBeClaimed({tabId:'tab-b',expiresAt:101},'tab-a',100),false))
test('lease expires exactly at boundary',()=>assert.equal(leaseCanBeClaimed({tabId:'tab-b',expiresAt:100},'tab-a',100),true))
