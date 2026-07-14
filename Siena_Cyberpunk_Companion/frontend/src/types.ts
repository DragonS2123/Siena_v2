export type Priority = 'P0_CRITICAL'|'P1_HIGH'|'P2_MEDIUM'|'P3_LOW'
export type Intent = 'continue'|'follow'|'hold'|'regroup'|'protect'|'retreat'|'stop'

export interface GameState {
  schema_version:string; session_id:string; sequence:number; captured_at:string;source:'simulator'|'cet';bridge_version:string|null
  game:{running:boolean;loaded:boolean;paused:boolean}
  player:{health:number;max_health:number;in_combat:boolean;in_vehicle:boolean;position:{x:number;y:number;z:number}}
  companion:{present:boolean;health:number;max_health:number;distance_to_player:number;current_intent:string;moving:boolean}
  environment:{district:string;visible_hostiles:number;highest_threat_id:string|null}
  derived?:{companion_stuck:boolean}
}
export interface GameEvent {event_id:string;session_id:string;event_type:string;priority:Priority;created_at:string;source:string;deduplication_key:string;payload:Record<string,unknown>}
export interface Command {command_id:string;intent:Intent;priority:Priority;created_at:string;valid_for_seconds:number;target_id:null;parameters:Record<string,unknown>;source:string}
export interface BridgeCapabilities {player_health:boolean;player_position:boolean;combat_state:boolean;vehicle_state:boolean;pause_state:boolean;district:boolean}
export interface BridgeStatus {connected:boolean;compatible:boolean;bridge_id:string|null;bridge_version:string|null;protocol_version:string|null;game_version:string|null;cet_version:string|null;last_hello_at:string|null;last_heartbeat_at:string|null;last_telemetry_at:string|null;last_sequence:number|null;session_id:string|null;capabilities:BridgeCapabilities;last_error:string|null;latency_ms:number|null;telemetry_rate:number;dropped_stale_states:number;source_conflict:boolean;active_source:'cet'|'simulator';configured_source:'auto'|'cet'|'simulator';registered_bridges:number}
export interface Status {backend:string;active_session:string|null;last_packet_at:string|null;active_source?:'cet'|'simulator';bridge?:BridgeStatus;scheduler?:{mode:string;reason:string|null;command:Command|null}}
export type Envelope = {type:'state';data:GameState}|{type:'event';data:GameEvent}|{type:'command';data:Command}|{type:'status';data:Status}|{type:'bridge_status';data:BridgeStatus}
