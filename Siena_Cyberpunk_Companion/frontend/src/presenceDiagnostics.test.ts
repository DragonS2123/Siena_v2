import assert from 'node:assert/strict'
import test from 'node:test'
import {presenceFromBackendStatus,presenceFromEnvelope,presenceFromRest} from './presenceDiagnostics.ts'
import type {InGamePresenceStatus} from './types.ts'

const presence:InGamePresenceStatus={enabled:true,active:true,revision:42,state:'reaction',consumer_connected:true,last_poll_at:'2026-07-15T18:00:00Z',last_revision_sent:42,poll_count:120,unchanged_count:113,last_error:null,current_text_preview:'Здоровье 8%. Осторожно — 20%.',overlay_supported:true,overlay_enabled:true,overlay_version:'0.7.0',font_cyrillic_ready:false,poll_interval_ms:500}

test('REST presence diagnostics accepts the strict status shape',()=>assert.deepEqual(presenceFromRest(presence),presence))
test('REST presence diagnostics rejects malformed data',()=>assert.equal(presenceFromRest({enabled:true}),null))
test('presence status updates through the existing websocket envelope',()=>assert.deepEqual(presenceFromEnvelope({type:'in_game_presence_status',data:presence,payload:presence}),presence))
test('periodic backend status can refresh presence diagnostics',()=>assert.deepEqual(presenceFromEnvelope({type:'status',data:{backend:'online',active_session:null,last_packet_at:null,presence}}),presence))
test('unrelated existing websocket messages do not alter presence',()=>assert.equal(presenceFromEnvelope({type:'status',data:{backend:'online',active_session:null,last_packet_at:null}}),null))
test('backend status bootstrap exposes presence without controls',()=>assert.deepEqual(presenceFromBackendStatus({backend:'online',active_session:null,last_packet_at:null,presence}),presence))

