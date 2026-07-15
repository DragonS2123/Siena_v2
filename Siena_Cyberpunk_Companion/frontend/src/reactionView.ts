import type {GameEvent,ReactionGenerationStatus,SienaReaction} from './types'

export const CRITICAL_HOLD_MS=10_000

const rank={low:0,medium:1,high:2,critical:3} as const

export function eventReactionPriority(event:GameEvent|undefined):SienaReaction['priority']{
  if(!event)return 'low'
  if(event.priority==='P0_CRITICAL'||event.severity==='critical')return 'critical'
  if(event.priority==='P1_HIGH'||event.severity==='high')return 'high'
  if(event.priority==='P2_MEDIUM'||event.severity==='medium')return 'medium'
  return 'low'
}

export function selectLiveReaction(reactions:SienaReaction[],sessionId:string|null,now:number):SienaReaction|null{
  const current=reactions.filter(item=>item.session_id===sessionId)
  const newest=current[0]??null
  if(!newest||newest.priority==='critical')return newest
  const held=current.find(item=>item.priority==='critical'&&now-new Date(item.created_at).getTime()<CRITICAL_HOLD_MS)
  return held??newest
}

export function lifecycleCanReplace(
  live:SienaReaction|null,
  lifecycle:ReactionGenerationStatus|null,
  events:GameEvent[],
  now:number,
):boolean{
  if(!lifecycle||!['queued','generating'].includes(lifecycle.state))return false
  if(!live)return true
  const incoming=eventReactionPriority(events.find(item=>item.event_id===lifecycle.event_id))
  if(incoming==='critical')return true
  const criticalHeld=live.priority==='critical'&&now-new Date(live.created_at).getTime()<CRITICAL_HOLD_MS
  return !criticalHeld&&rank[incoming]>=rank[live.priority]
}
