export type Priority = 'P0_CRITICAL'|'P1_HIGH'|'P2_MEDIUM'|'P3_LOW'
export type Severity = 'info'|'low'|'medium'|'high'|'critical'
export type Intent = 'continue'|'follow'|'hold'|'regroup'|'protect'|'retreat'|'stop'

export interface GameState {
  schema_version:string; session_id:string; sequence:number; captured_at:string;source:'simulator'|'cet';bridge_version:string|null
  game:{running:boolean;loaded:boolean;paused:boolean}
  player:{health:number;max_health:number;in_combat:boolean;in_vehicle:boolean;position:{x:number;y:number;z:number}}
  companion:{present:boolean;health:number;max_health:number;distance_to_player:number;current_intent:string;moving:boolean}
  environment:{district:string;visible_hostiles:number;highest_threat_id:string|null}
  derived?:{companion_stuck:boolean}
}
export interface GameEvent {event_id:string;session_id:string;event_type:string;priority:Priority;severity:Severity;created_at:string;source:string;sequence:number|null;summary:string;deduplication_key:string;payload:Record<string,unknown>}
export type ScenePhase='session_start'|'exploration'|'combat'|'danger'|'recovery'|'vehicle'|'idle'|'transition'|'session_end'|'unknown'
export type ReactionFocus='session_greeting'|'danger_warning'|'combat_comment'|'recovery_comment'|'exploration_comment'|'vehicle_comment'|'idle_comment'|'scene_resolution'
export interface SceneContext {scene_id:string;session_id:string;phase:ScenePhase;previous_phase:ScenePhase|null;severity:Severity;peak_severity:Severity;health_percent:number|null;health_trend:string;combat_state:boolean|null;vehicle_state:boolean|null;summary:string;revision:number;reaction_count:number;started_at:string;updated_at:string;closed_at:string|null}
export interface SienaReaction {reaction_id:string;event_id:string;session_id:string|null;event_type:string;text:string;created_at:string;priority:'low'|'medium'|'high'|'critical';provider:string;requested_provider:string|null;fallback_used:boolean;fallback_reason:string|null;latency_ms:number|null;model:string|null;request_id:string|null;scene_id:string|null;scene_revision:number|null;scene_phase:ScenePhase|null;focus:ReactionFocus|null;metadata:Record<string,unknown>}
export type ReactionGenerationState = 'queued'|'generating'|'completed'|'fallback'|'suppressed'|'failed'
export interface ReactionGenerationStatus {request_id:string;event_id:string;session_id:string;event_type:string;state:ReactionGenerationState;scene_id:string|null;scene_phase:ScenePhase|null;focus:ReactionFocus|null;reason:string|null}
export interface PlannerStatus {enabled:boolean;provider:string;queued_events:number;event_count:number;reaction_count:number;last_event_at:string|null;last_reaction_at:string|null;cooldown_remaining_seconds:number}
export interface ReactionProviderStatus {enabled:boolean;configured_provider:string;active_provider:string;fallback_provider:string;configuration_error:string|null;siena_core_reachable:boolean|null;circuit_state:'closed'|'open'|'half_open';consecutive_failures:number;queue_size:number;queue_capacity:number;worker_running:boolean;last_request_at:string|null;last_success_at:string|null;last_failure_at:string|null;last_error:string|null;average_latency_ms:number|null;fallback_count:number;stale_suppressed_count:number}
export interface Command {command_id:string;intent:Intent;priority:Priority;created_at:string;valid_for_seconds:number;target_id:null;parameters:Record<string,unknown>;source:string}
export interface BridgeCapabilities {player_health:boolean;player_position:boolean;combat_state:boolean;vehicle_state:boolean;pause_state:boolean;district:boolean}
export interface BridgeStatus {connected:boolean;compatible:boolean;bridge_id:string|null;bridge_version:string|null;protocol_version:string|null;game_version:string|null;cet_version:string|null;last_hello_at:string|null;last_heartbeat_at:string|null;last_telemetry_at:string|null;last_sequence:number|null;session_id:string|null;capabilities:BridgeCapabilities;last_error:string|null;latency_ms:number|null;telemetry_rate:number;dropped_stale_states:number;source_conflict:boolean;active_source:'cet'|'simulator';configured_source:'auto'|'cet'|'simulator';registered_bridges:number}
export interface Status {backend:string;active_session:string|null;last_packet_at:string|null;active_source?:'cet'|'simulator';bridge?:BridgeStatus;planner?:PlannerStatus;reaction_provider?:ReactionProviderStatus;scene?:SceneContext|null;scheduler?:{mode:string;reason:string|null;command:Command|null}}
export type Envelope = {type:'state';data:GameState}|{type:'event'|'game_event';data:GameEvent;payload?:GameEvent}|{type:'siena_reaction';data:SienaReaction;payload?:SienaReaction}|{type:'reaction_generation_status';data:ReactionGenerationStatus;payload?:ReactionGenerationStatus}|{type:'reaction_provider_status';data:ReactionProviderStatus;payload?:ReactionProviderStatus}|{type:'scene_context_updated';data:SceneContext;payload?:SceneContext}|{type:'command';data:Command}|{type:'status';data:Status}|{type:'bridge_status';data:BridgeStatus}
