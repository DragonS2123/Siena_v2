import type {Envelope,InGamePresenceStatus,Status} from './types'

export function presenceFromEnvelope(envelope:Envelope):InGamePresenceStatus|null{
  if(envelope.type==='in_game_presence_status')return envelope.payload??envelope.data
  if(envelope.type==='status')return envelope.data.presence??null
  return null
}

export function presenceFromRest(value:unknown):InGamePresenceStatus|null{
  if(!value||typeof value!=='object')return null
  const item=value as Partial<InGamePresenceStatus>
  if(typeof item.enabled!=='boolean'||typeof item.revision!=='number'||typeof item.state!=='string'||typeof item.consumer_connected!=='boolean')return null
  return item as InGamePresenceStatus
}

export function presenceFromBackendStatus(value:Status):InGamePresenceStatus|null{
  return value.presence??null
}

