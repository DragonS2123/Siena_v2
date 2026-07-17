import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import type {VoiceClipReady,VoiceGenerationStatus,VoicePriority,VoiceStatus} from './types'

const HTTP=(import.meta as ImportMeta & {env?:Record<string,string>}).env?.VITE_API_URL ?? 'http://127.0.0.1:8765'
const LEASE_KEY='siena-cp-voice-leader'
const LEASE_MS=6000
const RANK:Record<VoicePriority,number>={critical:0,high:1,medium:2,low:3,info:4}

export interface PlaybackLease{tabId:string;expiresAt:number}

export function leaseCanBeClaimed(lease:PlaybackLease|null,tabId:string,now:number){return !lease||lease.tabId===tabId||lease.expiresAt<=now}
export function isPlayableClip(clip:VoiceClipReady,sessionId:string|null,now:number){return clip.session_id===sessionId&&new Date(clip.expires_at).getTime()>now}
export function enqueueVoiceClip(queue:VoiceClipReady[],clip:VoiceClipReady,limit=10){
  if(queue.some(item=>item.reaction_id===clip.reaction_id||item.voice_clip_id===clip.voice_clip_id))return queue
  return [...queue,clip].sort((a,b)=>RANK[a.priority]-RANK[b.priority]).slice(0,limit)
}
export function shouldInterrupt(current:VoiceClipReady|null,incoming:VoiceClipReady,mode:'never'|'critical_only'){
  return mode==='critical_only'&&incoming.priority==='critical'&&!!current&&['info','low','medium'].includes(current.priority)
}

export function useVoicePlayback({clips,generation,sessionId,status}:{clips:VoiceClipReady[];generation:VoiceGenerationStatus|null;sessionId:string|null;status:VoiceStatus|null}){
  const tabId=useRef(crypto.randomUUID())
  const [enabled,setEnabled]=useState(()=>localStorage.getItem('siena-cp-voice-enabled')==='true')
  const [unlocked,setUnlocked]=useState(false)
  const [muted,setMuted]=useState(()=>localStorage.getItem('siena-cp-voice-muted')==='true')
  const [volume,setVolumeState]=useState(()=>Number(localStorage.getItem('siena-cp-voice-volume')??'0.85'))
  const [queue,setQueue]=useState<VoiceClipReady[]>([])
  const [current,setCurrent]=useState<VoiceClipReady|null>(null)
  const [leader,setLeader]=useState(false)
  const [error,setError]=useState('')
  const audio=useRef<HTMLAudioElement|null>(null)
  const currentRef=useRef<VoiceClipReady|null>(null)
  const processed=useRef(new Set<string>())
  const gap=useRef<number|undefined>(undefined)
  const [tick,setTick]=useState(0)

  useEffect(()=>{currentRef.current=current},[current])
  const acknowledge=useCallback(async(clip:VoiceClipReady,event:string,reason?:string)=>{
    try{const response=await fetch(`${HTTP}/api/v1/voice/playback-events`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice_clip_id:clip.voice_clip_id,event,tab_id:tabId.current,reason:reason??null})});return response.ok}catch{return false}
  },[])

  useEffect(()=>{
    if(!enabled||!unlocked){setLeader(false);return}
    const channel=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('siena-cp-voice')
    const claim=()=>{try{const now=Date.now();const raw=localStorage.getItem(LEASE_KEY);const lease=raw?JSON.parse(raw) as PlaybackLease:null;if(leaseCanBeClaimed(lease,tabId.current,now)){localStorage.setItem(LEASE_KEY,JSON.stringify({tabId:tabId.current,expiresAt:now+LEASE_MS}));setLeader(true);channel?.postMessage({type:'leader',tabId:tabId.current})}else setLeader(false)}catch{setLeader(true)}}
    claim();const timer=window.setInterval(claim,2000)
    const release=()=>{try{const lease=JSON.parse(localStorage.getItem(LEASE_KEY)??'null') as PlaybackLease|null;if(lease?.tabId===tabId.current)localStorage.removeItem(LEASE_KEY)}catch{/* ignore */}}
    window.addEventListener('beforeunload',release)
    return()=>{window.clearInterval(timer);release();channel?.close();window.removeEventListener('beforeunload',release)}
  },[enabled,unlocked])

  useEffect(()=>{
    for(const clip of clips){
      if(processed.current.has(clip.voice_clip_id)||!isPlayableClip(clip,sessionId,Date.now()))continue
      processed.current.add(clip.voice_clip_id)
      if(shouldInterrupt(currentRef.current,clip,status?.interrupt_mode??'critical_only')){
        const interrupted=currentRef.current
        audio.current?.pause();audio.current=null;setCurrent(null)
        if(interrupted)void acknowledge(interrupted,'playback_cancelled','interrupted_by_critical')
      }
      setQueue(old=>enqueueVoiceClip(old,clip))
    }
  },[clips,sessionId,status?.interrupt_mode,acknowledge])

  useEffect(()=>{
    if(generation&&['suppressed','failed','cancelled'].includes(generation.state))setQueue(old=>old.filter(clip=>clip.voice_request_id!==generation.voice_request_id))
    if(sessionId)setQueue(old=>old.filter(clip=>clip.session_id===sessionId))
    else setQueue([])
  },[generation,sessionId])

  useEffect(()=>{
    if(!enabled||!unlocked||muted||!leader||current||queue.length===0)return
    const clip=queue[0]
    if(!isPlayableClip(clip,sessionId,Date.now())){setQueue(old=>old.slice(1));return}
    let cancelled=false
    const play=async()=>{
      try{
        const response=await fetch(`${HTTP}${clip.audio_url}`)
        if(!response.ok)throw new Error(`audio ${response.status}`)
        const blob=await response.blob();if(cancelled)return
        const url=URL.createObjectURL(blob);const element=new Audio(url);audio.current=element;element.volume=volume
        element.onended=()=>{URL.revokeObjectURL(url);audio.current=null;setCurrent(null);void acknowledge(clip,'playback_completed');gap.current=window.setTimeout(()=>setTick(value=>value+1),status?.post_play_gap_ms??250)}
        element.onerror=()=>{URL.revokeObjectURL(url);audio.current=null;setCurrent(null);setError('Playback failed');void acknowledge(clip,'playback_failed','media_error');setTick(value=>value+1)}
        setQueue(old=>old.filter(item=>item.voice_clip_id!==clip.voice_clip_id));setCurrent(clip)
        if(!await acknowledge(clip,'playback_started'))throw new Error('Playback is owned by another tab')
        await element.play();setError('')
      }catch(reason){audio.current=null;setCurrent(null);setUnlocked(false);setError(reason instanceof Error?reason.message:'Playback blocked by browser');void acknowledge(clip,'playback_failed','autoplay_rejected')}
    }
    void play();return()=>{cancelled=true}
  },[enabled,unlocked,muted,leader,current,queue,sessionId,volume,status?.post_play_gap_ms,acknowledge,tick])

  const enable=useCallback(async()=>{setError('');try{const AudioContextClass=window.AudioContext;const context=new AudioContextClass();await context.resume();await context.close();setEnabled(true);setUnlocked(true);localStorage.setItem('siena-cp-voice-enabled','true')}catch{setEnabled(true);setUnlocked(true);localStorage.setItem('siena-cp-voice-enabled','true')}},[])
  const disable=useCallback(()=>{audio.current?.pause();audio.current=null;if(currentRef.current)void acknowledge(currentRef.current,'playback_cancelled','voice_disabled');setCurrent(null);setQueue([]);setEnabled(false);setUnlocked(false);localStorage.setItem('siena-cp-voice-enabled','false')},[acknowledge])
  const toggleMute=useCallback(()=>setMuted(value=>{const next=!value;localStorage.setItem('siena-cp-voice-muted',String(next));return next}),[])
  const setVolume=useCallback((value:number)=>{const next=Math.min(1,Math.max(0,value));setVolumeState(next);if(audio.current)audio.current.volume=next;localStorage.setItem('siena-cp-voice-volume',String(next))},[])
  const clearQueue=useCallback(()=>setQueue([]),[])
  useEffect(()=>()=>{if(gap.current)window.clearTimeout(gap.current);audio.current?.pause()},[])
  const state=useMemo(()=>!status?.enabled?'disabled':status.circuit_state==='open'?'unavailable':!enabled||!unlocked?'click_to_enable':muted?'muted':current?'playing':status.currently_synthesizing?'synthesizing':'ready',[status,enabled,unlocked,muted,current])
  return{enabled,unlocked,muted,volume,queue,current,isLeader:leader,error,state,enable,disable,toggleMute,setVolume,clearQueue}
}
