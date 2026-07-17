import type {ContextualCompanionStatus} from './types'

export function formatContextualCompanion(value:ContextualCompanionStatus|null|undefined){
  return {
    state:value?.situation_state?.replaceAll('_',' ')??'—', urgency:value?.reaction_urgency??'—',
    pending:value?.primary_pending_event?.replaceAll('_',' ')??'—',
    grouped:(value?.grouped_related_events??[]).slice(0,8).map(item=>item.replaceAll('_',' ')).join(' · ')||'—',
    queue:value?.queue_size??0,lastCategory:value?.last_emitted_reaction_category?.replaceAll('_',' ')??'—',
    suppression:value?.last_suppression_reason?.replaceAll('_',' ')??'—',window:value?.event_window_size??0,
  }
}
