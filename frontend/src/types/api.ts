/* eslint-disable @typescript-eslint/no-explicit-any */
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
  status: 'running';
  events_url: string;
}

export interface OAuthStartResponse {
  provider: string;
  authorization_url: string;
  state: string;
  expires_at: string;
}

export interface TaskNode {
  client_node_id: string;
  title: string;
  description?: string | null;
  verb: string;
  estimated_minutes: number;
  node_type: 'group' | 'action';
  depends_on?: string[];
  children?: TaskNode[];
}

export interface TaskTree {
  root: TaskNode;
  summary: string;
  assumptions?: string[];
}

export interface ThreadSnapshot {
  thread_id: string;
  status: string;
  state_version: number;
  last_event_id: string | null;
  server_time: string;
  intent_text: string;
  task_tree?: any | null;
  interrupt_payload?: any | null;
  latest_checkpoint_id?: string | null;
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
  input?: any;
  ctx?: Record<string, any>;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}
