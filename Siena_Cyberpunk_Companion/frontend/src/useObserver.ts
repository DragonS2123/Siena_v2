import {useCallback,useEffect,useRef,useState} from 'react'
import type {BridgeStatus,Command,Envelope,GameEvent,GameState,Intent,Priority,SienaReaction,Status} from './types'

const HTTP=import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8765'
const WS=HTTP.replace(/^http/,'ws')+'/ws'
const MAX_EVENTS=200
const MAX_REACTIONS=100

export function useObserver(){
  const [state,setState]=useState<GameState|null>(null)
  const [events,setEvents]=useState<GameEvent[]>([])
  const [reactions,setReactions]=useState<SienaReaction[]>([])
  const [command,setCommand]=useState<Command|null>(null)
  const [status,setStatus]=useState<Status>({backend:'offline',active_session:null,last_packet_at:null})
  const [bridge,setBridge]=useState<BridgeStatus|null>(null)
  const [socketState,setSocketState]=useState<'connecting'|'online'|'offline'>('connecting')
  const retry=useRef(0)

  useEffect(()=>{
    let cancelled=false, socket:WebSocket|undefined, timer:number|undefined
    const bootstrap=async()=>{try{const [s,e,r,c,st,b]=await Promise.all([fetch(`${HTTP}/api/v1/telemetry/latest`),fetch(`${HTTP}/api/v1/events?limit=200`),fetch(`${HTTP}/api/v1/reactions?limit=100`),fetch(`${HTTP}/api/v1/commands/current`),fetch(`${HTTP}/api/v1/status`),fetch(`${HTTP}/api/v1/bridge/status`)]);if(!cancelled){if(s.ok)setState(await s.json());if(e.ok)setEvents(await e.json());if(r.ok)setReactions(await r.json());if(c.ok)setCommand(await c.json());if(st.ok){const value=await st.json() as Status;setStatus(value);if(value.bridge)setBridge(value.bridge)}if(b.ok)setBridge(await b.json())}}catch{if(!cancelled)setStatus(v=>({...v,backend:'offline'}))}}
    const connect=()=>{if(cancelled)return;setSocketState('connecting');socket=new WebSocket(WS)
      socket.onopen=()=>{retry.current=0;setSocketState('online')}
      socket.onmessage=message=>{try{const envelope=JSON.parse(message.data) as Envelope;if(envelope.type==='state')setState(envelope.data);if(envelope.type==='event'||envelope.type==='game_event'){const event=envelope.payload??envelope.data;setEvents(old=>[event,...old.filter(e=>e.event_id!==event.event_id)].slice(0,MAX_EVENTS))}if(envelope.type==='siena_reaction'){const reaction=envelope.payload??envelope.data;setReactions(old=>[reaction,...old.filter(item=>item.reaction_id!==reaction.reaction_id)].slice(0,MAX_REACTIONS))}if(envelope.type==='command')setCommand(envelope.data);if(envelope.type==='status'){setStatus(envelope.data);if(envelope.data.bridge)setBridge(envelope.data.bridge)}if(envelope.type==='bridge_status')setBridge(envelope.data)}catch{/* malformed server messages do not crash the panel */}}
      socket.onclose=()=>{if(cancelled)return;setSocketState('offline');const delay=Math.min(1000*2**retry.current++,15000);timer=window.setTimeout(connect,delay)}
      socket.onerror=()=>socket?.close()
    }
    void bootstrap();connect();return()=>{cancelled=true;if(timer)clearTimeout(timer);socket?.close()}
  },[])

  const sendCommand=useCallback(async(intent:Intent)=>{const priority:Priority=intent==='retreat'||intent==='stop'?'P0_CRITICAL':'P1_HIGH';const body:Command={command_id:crypto.randomUUID(),intent,priority,created_at:new Date().toISOString(),valid_for_seconds:30,target_id:null,parameters:{reason:'operator'},source:'manual'};const response=await fetch(`${HTTP}/api/v1/commands`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw new Error(`Command rejected (${response.status})`);setCommand(await response.json())},[])
  return {state,events,setEvents,reactions,command,status,bridge,socketState,sendCommand}
}
