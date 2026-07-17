import assert from 'node:assert/strict'
import test from 'node:test'
import {formatContextualCompanion} from './contextualCompanionView.ts'

test('contextual diagnostics render old payloads and null state safely',()=>{const view=formatContextualCompanion(null);assert.equal(view.state,'—');assert.equal(view.window,0)})
test('contextual diagnostics expose suppression and bounded grouping',()=>{const view=formatContextualCompanion({enabled:true,session_id:'s',situation_state:'combat',reaction_urgency:'warning',primary_pending_event:'player_ram_low',grouped_related_events:Array(10).fill('weapon_changed'),queue_size:1,last_emitted_reaction_category:'health',last_suppression_reason:'grouping_wait',event_window_size:20});assert.equal(view.suppression,'grouping wait');assert.equal(view.grouped.split(' · ').length,8)})
