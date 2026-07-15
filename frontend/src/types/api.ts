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
  completion_rule: 'all_ai_actions_completed' | 'long_term_execution_gate';
  estimated_duration_weeks?: number | null;
}

export interface PracticeLoopDefinition {
  loop_id: string;
  title: string;
  target_per_week: number;
  duration_weeks: number;
  done_criteria: string;
}

export interface OutcomeCheckpoint {
  checkpoint_id: string;
  title: string;
  evidence_type: 'numeric' | 'artifact' | 'self_assessment';
  unit?: string | null;
  operator: 'gte' | 'lte' | 'exists';
  target_value?: number | null;
}

export interface PhaseGate {
  process_threshold: 0.8;
  outcome_rule: 'all_required';
}

export interface PlanningContext {
  schema_version: 1 | 2;
  intent_type: 'long_term_growth' | 'exploration_decision';
  time_horizon: 'minutes' | 'hours' | 'days' | 'weeks' | 'months';
  roadmap: RoadmapPhase[];
  current_phase: CurrentPhase | null;
  next_action_client_node_id: string | null;
  practice_loops?: PracticeLoopDefinition[];
  outcome_checkpoints?: OutcomeCheckpoint[];
  phase_gate?: PhaseGate | null;
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
  strategy_context?: StrategyContext | null;
}

export interface PracticeLoopProgress {
  loop_id: string;
  loop_key: string;
  title: string;
  done_criteria: string;
  target_per_week: number;
  current_week_completed: number;
  total_completed: number;
  required_completions: number;
  estimated_end: string;
  status: 'active' | 'paused' | 'completed' | 'superseded';
  can_schedule_today: boolean;
  active_occurrence_task_id: string | null;
}

export interface PhaseReview {
  id: string;
  phase_id: string;
  status: 'draft' | 'finalized';
  recommendation: 'ready' | 'partial' | 'not_ready' | 'overridden';
  decision: 'proceed' | 'extend' | 'adjust' | 'override' | null;
  evidence: Record<string, Record<string, unknown>>;
  difficulty: string | null;
  next_capacity: string | null;
  override_reason: string | null;
  statistics: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LongTermExecutionSnapshot {
  phase_id: string;
  recommendation: 'ready' | 'partial' | 'not_ready' | 'overridden';
  review_available: boolean;
  one_off_ready: boolean;
  process_ready: boolean;
  outcome_ready: boolean;
  loops: PracticeLoopProgress[];
  active_review: PhaseReview | null;
  latest_finalized_review: PhaseReview | null;
  review_history: PhaseReview[];
}

export interface PhaseReviewUpdateRequest {
  evidence: Record<string, Record<string, unknown>>;
  difficulty?: string | null;
  next_capacity?: string | null;
  early_review_requested?: boolean;
}

export interface PhaseReviewDecisionRequest {
  decision: 'proceed' | 'extend' | 'adjust' | 'override';
  override_reason?: string | null;
  extension_weeks?: number | null;
  adjustments?: Array<{
    loop_id: string;
    title?: string | null;
    target_per_week?: number | null;
    done_criteria?: string | null;
  }>;
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
  long_term_execution?: LongTermExecutionSnapshot | null;
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
  source?: 'ai' | 'manual' | 'practice_loop' | 'task_assist';
  phase_id?: string | null;
  phase_order?: number | null;
  practice_loop_id?: string | null;
  assist_rollup?: boolean;
  assist_request_id?: string | null;
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
  run_type: 'initial' | 'next_phase' | 'refine';
  request_id: string;
  state_version: number;
}

export type AgentRunType = 'initial' | 'next_phase' | 'refine';

export interface ActiveRun {
  threadId: string;
  runType: AgentRunType;
  requestId: string;
}

export interface SSEEventEnvelope {
  event_id: string;
  thread_id: string;
  request_id: string;
  run_type: AgentRunType | 'task_assist' | 'execution_refine';
  event_type: string;
  seq: number;
  created_at: string;
  payload: {
    stage?: string;
    label?: string;
    state_version?: number;
    [key: string]: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  };
}

export interface DeliverableDefinition {
  title: string;
  format: string;
  quality_bar: string[];
}

export interface DeadlineDefinition {
  text: string;
  is_explicit: boolean;
}

export interface DeliveryTimePlan {
  available_minutes?: number | null;
  planned_minutes: number;
  buffer_minutes: number;
}

export interface DeliveryScope {
  must_have: string[];
  should_have: string[];
  can_cut: string[];
}

export interface DeliveryWorkstream {
  workstream_id: string;
  title: string;
  output: string;
  task_client_node_ids: string[];
}

export interface DeliveryStrategyContext {
  schema_version: 1;
  strategy_type: 'delivery';
  deliverable: DeliverableDefinition;
  deadline: DeadlineDefinition;
  time_plan: DeliveryTimePlan;
  scope: DeliveryScope;
  workstreams: DeliveryWorkstream[];
  critical_path_client_node_ids: string[];
}

export type DecisionDirection = 'continue_exploring' | 'pause_and_reassess' | 'not_recommended_now';
export type DecisionConfidence = 'low' | 'medium' | 'high';

export interface CurrentJudgment {
  direction: DecisionDirection;
  statement: string;
  confidence: DecisionConfidence;
}

export interface DecisionBasis {
  statement: string;
  basis_type: 'user_context' | 'known_constraint' | 'working_assumption';
}

export interface DecisionExperiment {
  experiment_id: string;
  title: string;
  hypothesis: string;
  success_signal: string;
  effort_level: 'low' | 'medium' | 'high';
  task_client_node_ids: string[];
}

export interface DecisionGate {
  review_after: string;
  proceed_if: string[];
  stop_if: string[];
}

export interface DecisionStrategyContext {
  schema_version: 1;
  strategy_type: 'decision';
  question: string;
  options: string[];
  current_judgment: CurrentJudgment;
  basis: DecisionBasis[];
  missing_information: string[];
  experiments: DecisionExperiment[];
  decision_gate: DecisionGate;
}

export type StrategyContext = DeliveryStrategyContext | DecisionStrategyContext;

export type TaskAssistMode = 'start' | 'unstick' | 'decompose';

export type TaskAssistRunStatus =
  | 'running'
  | 'ready'
  | 'applied'
  | 'cancelled'
  | 'failed'
  | 'expired';

export type TaskAssistStage =
  | 'queued'
  | 'context_ready'
  | 'generating'
  | 'validating'
  | 'ready'
  | 'applied'
  | 'cancelled'
  | 'failed'
  | 'expired';

export interface AssistTaskDraft {
  draft_id: string;
  title: string;
  description: string | null;
  estimated_minutes: number;
  done_criteria: string;
  start_hint: string | null;
  fallback_action: string | null;
}

export interface StartAssistProposal {
  schema_version: 1;
  proposal_type: 'start';
  summary: string;
  starter_step: AssistTaskDraft;
}

export interface RescueOption {
  option_id: string;
  title: string;
  action: string;
  estimated_minutes: number;
  tradeoff: string;
}

export interface UnstickAssistProposal {
  schema_version: 1;
  proposal_type: 'unstick';
  obstacle_summary: string;
  recommended_option_id: string;
  options: RescueOption[];
}

export interface AssistDraftDependency {
  task_draft_id: string;
  depends_on_draft_id: string;
}

export interface DecomposeAssistProposal {
  schema_version: 1;
  proposal_type: 'decompose';
  summary: string;
  completion_rule: 'all_subtasks_completed';
  subtasks: AssistTaskDraft[];
  dependencies: AssistDraftDependency[];
}

export type TaskAssistProposal =
  | StartAssistProposal
  | UnstickAssistProposal
  | DecomposeAssistProposal;

export interface TaskAssistRequest {
  request_id: string;
  mode: TaskAssistMode;
  user_context?: string | null;
}

export interface TaskAssistStartResponse {
  task_id: string;
  thread_id: string;
  request_id: string;
  mode: TaskAssistMode;
  status: TaskAssistRunStatus;
  events_url: string;
}

export interface TaskAssistRunSnapshot {
  task_id: string;
  thread_id: string;
  request_id: string;
  mode: TaskAssistMode;
  status: TaskAssistRunStatus;
  stage: TaskAssistStage | null;
  proposal: TaskAssistProposal | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
}

export interface TaskAssistApplyRequest {
  selected_option_id?: string | null;
}

export interface TaskAssistErrorResponse {
  error_code: string;
  message: string;
}

export interface TaskAssistApplyReceipt {
  request_id: string;
  proposal_type: TaskAssistMode;
  applied_at: string;
  affected_task_ids: string[];
}

export interface TaskAssistApplyResponse {
  status: 'applied';
  task: TaskResponse;
  tasks: TaskResponse[];
  apply_receipt: TaskAssistApplyReceipt;
}

// Execution Refine Types
export type ExecutionRefineMode = 'time_budget' | 'progress_recovery' | 'context_change';

export interface ExecutionRefineRequest {
  request_id: string;
  mode: ExecutionRefineMode;
  available_minutes?: number | null;
  new_deadline?: string | null;
  priority_task_ids?: string[];
  blocked_task_ids?: string[];
  user_context?: string | null;
}

export type ExecutionDiffOperationType = 'update_task' | 'add_task' | 'reorder_siblings' | 'set_my_day';

export interface ExecutionTaskChanges {
  title?: string | null;
  description?: string | null;
  estimated_minutes?: number | null;
  done_criteria?: string | null;
  start_hint?: string | null;
  fallback_action?: string | null;
}

export interface UpdateTaskOperation {
  operation_type: 'update_task';
  task_id: string;
  changes: ExecutionTaskChanges;
  reason: string;
}

export interface AddTaskOperation {
  operation_type: 'add_task';
  draft_id: string;
  parent_task_id: string | null;
  title: string;
  description?: string | null;
  estimated_minutes: number;
  done_criteria: string;
  start_hint?: string | null;
  fallback_action?: string | null;
  depends_on_refs: string[];
  insert_after_task_id: string | null;
  reason: string;
}

export interface ReorderSiblingsOperation {
  operation_type: 'reorder_siblings';
  parent_task_id: string | null;
  ordered_task_ids: string[];
  reason: string;
}

export interface SetMyDayOperation {
  operation_type: 'set_my_day';
  task_id: string;
  is_in_my_day: boolean;
  reason: string;
}

export type ExecutionDiffOperation =
  | UpdateTaskOperation
  | AddTaskOperation
  | ReorderSiblingsOperation
  | SetMyDayOperation;

export interface ExecutionRefineProposal {
  schema_version: 1;
  proposal_type: 'execution_refine';
  mode: ExecutionRefineMode;
  summary: string;
  user_facing_reasons: string[];
  preserved_constraints: string[];
  operations: ExecutionDiffOperation[];
  focus_task_ids: string[];
  estimated_focus_minutes: number;
  buffer_minutes: number;
  warnings: string[];
}

export type ExecutionRefineRunStatus = 'running' | 'ready' | 'applied' | 'cancelled' | 'failed' | 'expired';

export interface ExecutionRefineStartResponse {
  thread_id: string;
  request_id: string;
  mode: ExecutionRefineMode;
  status: ExecutionRefineRunStatus;
  events_url: string;
}

export interface ExecutionRefineRunSnapshot {
  run_id: string;
  user_id: string;
  thread_id: string;
  request_id: string;
  mode: ExecutionRefineMode;
  status: ExecutionRefineRunStatus;
  stage: string | null;
  scope_fingerprint: string;
  proposal: ExecutionRefineProposal | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
  applied_at?: string | null;
  cancelled_at?: string | null;
}

export interface ExecutionRefineApplyRequest {
  expected_scope_fingerprint?: string | null;
}

export interface ExecutionRefineApplyReceipt {
  run_id: string;
  thread_id: string;
  request_id: string;
  applied_at: string;
  scope_fingerprint: string;
  affected_task_ids: string[];
  created_task_ids: string[];
  focus_task_ids: string[];
}
