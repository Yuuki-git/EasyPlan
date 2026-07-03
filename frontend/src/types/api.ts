/**
 * This file is manually generated based on docs/openapi.json
 * Updated for v1.1.0 (Auth + Multi-planner + Microsoft To Do)
 */

export type ConfirmationAction = 'approve' | 'edit' | 'refine' | 'reject';

export interface ConfirmationRequest {
  request_id: string;
  action: ConfirmationAction;
  task_tree?: TaskTree | null;
  feedback?: string | null;
  reason?: string | null;
}

export interface ConfirmationResponse {
  thread_id: string;
  request_id: string;
  status: string;
}

export interface IntegrationStatus {
  provider: string;
  display_name: string;
  status: string;
  is_integrated: boolean;
  external_account_id?: string | null;
}

export interface IntentCreateRequest {
  intent_text: string;
  preferred_provider?: string; // e.g., 'todoist', 'microsoft_todo'
  planner_provider?: 'openai' | 'deepseek' | 'xiaomi';
  planner_model?: string | null;
}

export interface IntentCreateResponse {
  thread_id: string;
  request_id: string;
  status: 'running';
  events_url: string;
}

export interface OAuthStartResponse {
  provider: string;
  authorization_url: string;
  state: string;
  expires_at: string;
}

export type RoadmapStatus = 'planned' | 'current' | 'completed';

export interface RoadmapPhase {
  phase_id: string;
  order: number;
  title: string;
  objective: string;
  status: RoadmapStatus;
}

export interface CurrentPhase {
  phase_id: string;
  title: string;
  objective: string;
  completion_rule: 'all_ai_actions_completed';
}

export interface PlanningContext {
  schema_version: 1;
  intent_type: 'long_term_growth' | 'exploration_decision';
  time_horizon: 'minutes' | 'hours' | 'days' | 'weeks' | 'months';
  roadmap: RoadmapPhase[];
  current_phase: CurrentPhase | null;
  next_action_client_node_id: string | null;
}

export interface NextPhaseRequest {
  request_id: string;
}

export interface NextPhaseResponse {
  thread_id: string;
  request_id: string;
  status: 'running' | 'awaiting_confirmation' | 'confirmed' | 'cancelled';
  events_url: string;
}

export interface PhaseHistoryItem {
  status: 'running' | 'awaiting_confirmation' | 'confirmed' | 'cancelled' | 'failed';
  updated_at: string;
}

export interface TaskTreeReviewEnvelope {
  type: 'task_tree_review';
  user_id: string;
  thread_id: string;
  task_tree: TaskTree;
  planning_mode?: 'initial' | 'next_phase';
  phase_request_id?: string | null;
  allowed_actions?: string[];
}

export interface PhaseGenerationEnvelope {
  type: 'phase_generation_state';
  request_id: string;
  status: 'running' | 'awaiting_confirmation' | 'confirmed' | 'cancelled' | 'failed';
  history?: Record<string, PhaseHistoryItem>;
}

export interface NextPhaseReviewEnvelope {
  type: 'next_phase_review';
  request_id: string;
  status: 'awaiting_confirmation' | 'confirming';
  task_tree: TaskTree;
  history?: Record<string, PhaseHistoryItem>;
}

export type InterruptPayload = TaskTreeReviewEnvelope | PhaseGenerationEnvelope | NextPhaseReviewEnvelope;

export interface TaskNode {
  client_node_id: string;
  title: string;
  description?: string | null;
  verb: string;
  estimated_minutes: number;
  node_type: 'group' | 'action';
  depends_on?: string[];
  children?: TaskNode[];
  done_criteria?: string | null;
  start_hint?: string | null;
  fallback_action?: string | null;
}

export interface TaskTree {
  root: TaskNode;
  summary: string;
  assumptions?: string[];
  planning_context?: PlanningContext | null;
}

export interface ThreadSnapshot {
  thread_id: string;
  status: string;
  state_version: number;
  last_event_id: string | null;
  server_time: string;
  intent_text: string;
  task_tree?: TaskTree | null;
  interrupt_payload?: InterruptPayload | null;
  latest_checkpoint_id?: string | null;
}

export interface NextPhaseCommitReceipt {
  thread_id: string;
  request_id: string;
  status:
    | 'confirmed'
    | 'incomplete'
    | 'running'
    | 'awaiting_confirmation'
    | 'confirming'
    | 'cancelled'
    | 'failed'
    | 'unknown';
  current_phase_id: string | null;
  task_tree: TaskTree | null;
  tasks: TaskResponse[];
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface TaskResponse {
  id: string;
  user_id: string;
  thread_id: string;
  parent_task_id: string | null;
  client_node_id: string;
  title: string;
  description: string | null;
  node_type: 'group' | 'action';
  status: string;
  view_bucket: string;
  estimated_minutes: number | null;
  sort_order: number;
  is_in_my_day: boolean;
  done_criteria?: string | null;
  start_hint?: string | null;
  fallback_action?: string | null;
  source?: 'ai' | 'manual';
  phase_id?: string | null;
  phase_order?: number | null;
}

export interface TaskUpdateRequest {
  title?: string | null;
  description?: string | null;
  status?: 'draft' | 'active' | 'today' | 'completed' | 'archived' | null;
  view_bucket?: 'planned' | 'my_day' | 'backlog' | null;
  estimated_minutes?: number | null;
  sort_order?: number | null;
  is_in_my_day?: boolean;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface AgentRunEventMeta {
  thread_id: string;
  run_type: 'initial' | 'next_phase';
  request_id: string;
  state_version: number;
}

export type AgentRunType = 'initial' | 'next_phase';

export interface ActiveRun {
  threadId: string;
  runType: AgentRunType;
  requestId: string;
}
