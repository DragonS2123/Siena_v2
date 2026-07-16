import {useCallback,useEffect,useRef,useState} from 'react'
import type {BridgeStatus,Command,Envelope,GameEvent,GameState,InGamePresenceStatus,Intent,Priority,ReactionGenerationStatus,ReactionProviderStatus,SceneContext,SienaReaction,Status,VoiceClip,VoiceClipReady,VoiceGenerationStatus,VoiceStatus} from './types'
import {presenceFromEnvelope,presenceFromRest} from './presenceDiagnostics'

const HTTP=import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8765'
const WS=HTTP.replace(/^http/,'ws')+'/ws'
const MAX_EVENTS=200
const MAX_REACTIONS=100
const asReady=(clip:VoiceClip):VoiceClipReady=>({voice_clip_id:clip.voice_clip_id,voice_request_id:clip.voice_request_id,reaction_id:clip.reaction_id,session_id:clip.session_id,scene_id:clip.scene_id,scene_revision:clip.scene_revision,focus:clip.focus,priority:clip.priority,audio_url:`/api/v1/voice/audio/${clip.voice_clip_id}`,content_type:clip.content_type,duration_ms:clip.duration_ms,tts_provider:clip.tts_provider,speaker:clip.speaker,expires_at:clip.expires_at})

export function useObserver(){
  const [state,setState]=useState<GameState|null>(null)
  const [events,setEvents]=useState<GameEvent[]>([])
  const [reactions,setReactions]=useState<SienaReaction[]>([])
  const [command,setCommand]=useState<Command|null>(null)
  const [status,setStatus]=useState<Status>({backend:'offline',active_session:null,last_packet_at:null})
  const [bridge,setBridge]=useState<BridgeStatus|null>(null)
  const [reactionProvider,setReactionProvider]=useState<ReactionProviderStatus|null>(null)
  const [reactionGeneration,setReactionGeneration]=useState<ReactionGenerationStatus|null>(null)
  const [scene,setScene]=useState<SceneContext|null>(null)
  const [voiceStatus,setVoiceStatus]=useState<VoiceStatus|null>(null)
  const [voiceGeneration,setVoiceGeneration]=useState<VoiceGenerationStatus|null>(null)
  const [voiceReady,setVoiceReady]=useState<VoiceClipReady[]>([])
  const [voiceClips,setVoiceClips]=useState<VoiceClip[]>([])
  const [presenceStatus,setPresenceStatus]=useState<InGamePresenceStatus|null>(null)
  const [socketState,setSocketState]=useState<'connecting'|'online'|'offline'>('connecting')
  const retry=useRef(0)

  useEffect(()=>{
    let cancelled=false, socket:WebSocket|undefined, timer:number|undefined
    const bootstrap=async()=>{try{const [s,e,r,p,c,st,b,sc,vs,vc,ps]=await Promise.all([fetch(`${HTTP}/api/v1/telemetry/latest`),fetch(`${HTTP}/api/v1/events?limit=200`),fetch(`${HTTP}/api/v1/reactions?limit=100`),fetch(`${HTTP}/api/v1/reaction-provider/status`),fetch(`${HTTP}/api/v1/commands/current`),fetch(`${HTTP}/api/v1/status`),fetch(`${HTTP}/api/v1/bridge/status`),fetch(`${HTTP}/api/v1/scenes/current`),fetch(`${HTTP}/api/v1/voice/status`),fetch(`${HTTP}/api/v1/voice/clips?limit=10`),fetch(`${HTTP}/api/v1/in-game-presence/status`)]);if(!cancelled){if(s.ok)setState(await s.json());if(e.ok)setEvents(await e.json());if(r.ok)setReactions(await r.json());if(p.ok)setReactionProvider(await p.json());if(c.ok)setCommand(await c.json());if(st.ok){const value=await st.json() as Status;setStatus(value);if(value.bridge)setBridge(value.bridge);if(value.reaction_provider)setReactionProvider(value.reaction_provider);if(value.scene)setScene(value.scene);if(value.voice)setVoiceStatus(value.voice);if(value.presence)setPresenceStatus(value.presence)}if(b.ok)setBridge(await b.json());if(sc.ok){const value=await sc.json() as {active:boolean;scene:SceneContext|null};setScene(value.scene)}if(vs.ok)setVoiceStatus(await vs.json());if(vc.ok){const clips=await vc.json() as VoiceClip[];setVoiceClips(clips);setVoiceReady(clips.filter(clip=>clip.state==='ready').map(asReady))}if(ps.ok){const value=presenceFromRest(await ps.json());if(value)setPresenceStatus(value)}}}catch{if(!cancelled)setStatus(v=>({...v,backend:'offline'}))}}
    const connect=()=>{if(cancelled)return;setSocketState('connecting');socket=new WebSocket(WS)
      socket.onopen=()=>{retry.current=0;setSocketState('online')}
      socket.onmessage=message=>{try{const envelope=JSON.parse(message.data) as Envelope;const presence=presenceFromEnvelope(envelope);if(presence)setPresenceStatus(presence);if(envelope.type==='state')setState(envelope.data);if(envelope.type==='event'||envelope.type==='game_event'){const event=envelope.payload??envelope.data;setEvents(old=>[event,...old.filter(e=>e.event_id!==event.event_id)].slice(0,MAX_EVENTS))}if(envelope.type==='siena_reaction'){const reaction=envelope.payload??envelope.data;setReactions(old=>[reaction,...old.filter(item=>item.reaction_id!==reaction.reaction_id)].slice(0,MAX_REACTIONS))}if(envelope.type==='reaction_generation_status')setReactionGeneration(envelope.payload??envelope.data);if(envelope.type==='reaction_provider_status')setReactionProvider(envelope.payload??envelope.data);if(envelope.type==='scene_context_updated')setScene(envelope.payload??envelope.data);if(envelope.type==='voice_generation_status'){const voice=envelope.payload??envelope.data;setVoiceGeneration(voice);if(['suppressed','failed','cancelled','completed'].includes(voice.state))setVoiceReady(old=>old.filter(clip=>clip.voice_request_id!==voice.voice_request_id));setVoiceClips(old=>old.map(clip=>clip.voice_request_id===voice.voice_request_id?{...clip,state:voice.state,error_category:voice.reason}:clip))}if(envelope.type==='voice_clip_ready'){const clip=envelope.payload??envelope.data;setVoiceReady(old=>[...old.filter(item=>item.reaction_id!==clip.reaction_id),clip].slice(-10));void fetch(`${HTTP}/api/v1/voice/clips/latest`).then(response=>response.ok?response.json():null).then((latest:VoiceClip|null)=>{if(latest)setVoiceClips(old=>[latest,...old.filter(item=>item.voice_clip_id!==latest.voice_clip_id)].slice(0,10))}).catch(()=>{})}if(envelope.type==='command')setCommand(envelope.data);if(envelope.type==='status'){setStatus(envelope.data);if(envelope.data.bridge)setBridge(envelope.data.bridge);if(envelope.data.reaction_provider)setReactionProvider(envelope.data.reaction_provider);if(envelope.data.scene)setScene(envelope.data.scene);if(envelope.data.voice)setVoiceStatus(envelope.data.voice)}if(envelope.type==='bridge_status')setBridge(envelope.data)}catch{/* malformed server messages do not crash the panel */}}
      socket.onclose=()=>{if(cancelled)return;setSocketState('offline');const delay=Math.min(1000*2**retry.current++,15000);timer=window.setTimeout(connect,delay)}
      socket.onerror=()=>socket?.close()
    }
    void bootstrap();connect();return()=>{cancelled=true;if(timer)clearTimeout(timer);socket?.close()}
  },[])

  const sendCommand=useCallback(async(intent:Intent)=>{const priority:Priority=intent==='retreat'||intent==='stop'?'P0_CRITICAL':'P1_HIGH';const body:Command={command_id:crypto.randomUUID(),intent,priority,created_at:new Date().toISOString(),valid_for_seconds:30,target_id:null,parameters:{reason:'operator'},source:'manual'};const response=await fetch(`${HTTP}/api/v1/commands`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw new Error(`Command rejected (${response.status})`);setCommand(await response.json())},[])
  return {state,events,setEvents,reactions,reactionGeneration,reactionProvider,scene,voiceStatus,voiceGeneration,voiceReady,voiceClips,presenceStatus,command,status,bridge,socketState,sendCommand}
}
