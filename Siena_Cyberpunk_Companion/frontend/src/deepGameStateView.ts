export function formatResource(current:number|null|undefined,maximum:number|null|undefined){
  if(current==null||maximum==null||!Number.isFinite(current)||!Number.isFinite(maximum)||current<0||maximum<=0){
    return {text:'—',percent:null}
  }
  const percent=Math.max(0,Math.min(100,current/maximum*100))
  return {text:`${current.toFixed(1)} / ${maximum.toFixed(1)} (${Math.round(percent)}%)`,percent}
}

export function shortenWeaponRecord(value:string|null|undefined){
  if(!value)return '—'
  const embedded=value.match(/--\[\[\s*(.*?)\s*--\]\]/)?.[1]?.trim()
  const stable=(embedded??value).trim()
  if(!stable||stable.toLowerCase().includes('userdata:'))return '—'
  const parts=stable.split('.')
  const short=parts.at(-1)??stable
  return short.length>44?`${short.slice(0,41)}…`:short
}

export type CyberdeckView={identity:string;quality:string;capacity:string;programs:{slot:string;record:string}[];truncated:boolean}

export function cyberdeckSlotNumber(value:string|null|undefined){
  return value?.match(/^AttachmentSlots\.CyberdeckProgram([1-8])$/)?.[1]??'—'
}

export function formatCyberdeck(
  deck:{record_id:string|null;quality:string|null;iconic:boolean|null;program_capacity:{used:number|null;total:number|null}|null;programs:{slot_id:string;record_id:string}[]|null;truncated:boolean|null}|null|undefined,
  identityAvailable:boolean|null|undefined,
):CyberdeckView{
  if(!deck)return {identity:identityAvailable===true?'No cyberdeck':'—',quality:'—',capacity:'—',programs:[],truncated:false}
  const short=shortenWeaponRecord(deck.record_id)
  const identity=short==='—'?'—':`${short}${deck.iconic===true?' · ICONIC':''}`
  const used=deck.program_capacity?.used
  const total=deck.program_capacity?.total
  const capacity=used==null&&total==null?'—':`${used??'?'} / ${total??'?'}`
  return {identity,quality:deck.quality??'—',capacity,programs:(deck.programs??[]).slice(0,8).map(program=>({slot:cyberdeckSlotNumber(program.slot_id),record:shortenWeaponRecord(program.record_id)})),truncated:deck.truncated===true}
}

export function formatBuildAwareness(profile:import('./types').BuildProfile|null|undefined){
  const loadout=profile?.quickhack_loadout
  const confidence=profile?.confidence
  const counts=loadout?.categories
  return {
    available:!!profile,
    style:profile?.summary_key?.replaceAll('_',' ')??'—',
    confidence:confidence==null||!Number.isFinite(confidence)?'—':`${Math.round(Math.max(0,Math.min(1,confidence))*100)}%`,
    dominant:loadout?.dominant_category??'—',
    knownUnknown:loadout?.known_programs==null&&loadout?.unknown_programs==null?'—':`${loadout?.known_programs??'?'} / ${loadout?.unknown_programs??'?'}`,
    counts:counts?Object.entries(counts).filter(([,value])=>value>0).map(([key,value])=>`${key} ${value}`).join(' · ')||'none':'—',
    evidence:(profile?.evidence??[]).slice(0,4),
    limitationCount:(profile?.limitations??[]).slice(0,12).length,
  }
}
