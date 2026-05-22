// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/** Embedding backend info from health endpoint */
export interface EmbeddingInfo {
	backend: string;
	model: string;
	dimensions: string;
	status: string; // "active" | "fallback" | "not_initialized"
}

/** Health check response from GET /health */
export interface HealthResponse {
	engine: string;
	sqlite: string;
	chromadb?: string;
	n8n: string;
	uptime_seconds: number;
	embeddings?: EmbeddingInfo;
}

/** WebSocket message from the engine */
export interface WsMessage {
	type:
		| 'card_created' | 'card_updated'
		| 'group_carried_forward' | 'group_summary_updated'
		| 'summary_updated'
		| 'budget_status'
		| 'omni_updated'
		| 'settings_changed'
		| 'open_compose'
		| 'processing_rule_auto_disabled'
		| 'push_notification'
		| (string & {});
	event_id?: string;
	card_id?: string;
	entity_id?: string;
	session_id?: string;
	summary?: GroupSummary;
	payload: Record<string, unknown>;
}

/** Team member from team.json */
export interface TeamMember {
	name: string;
	email: string;
	role: 'self' | 'manager' | 'teammate' | 'external' | 'bot';
	notes: string;
	aliases: string[];
	accounts: string[];
}

/** team.json structure */
export interface TeamConfig {
	members: TeamMember[];
}

/** Simple rule condition */
export interface SimpleCondition {
	field: string;
	operator: 'equals' | 'not_equals' | 'contains' | 'starts_with' | 'ends_with' | 'in';
	value: string | string[];
}

/** Compound "all" condition */
export interface AllCondition {
	all: RuleCondition[];
}

/** Compound "any" condition */
export interface AnyCondition {
	any: RuleCondition[];
}

/** Union of condition types */
export type RuleCondition = SimpleCondition | AllCondition | AnyCondition;

/** A single filter rule */
export interface Rule {
	name: string;
	enabled: boolean;
	condition: RuleCondition;
	action: 'drop' | 'allow';
}

/** rules.json structure */
export interface RulesConfig {
	rules: Rule[];
}

/** A user-defined classification rule (natural language) */
export interface ClassificationRule {
	id: number;
	space_id: string | null;
	field: string | null;
	rule_text: string;
	source: 'manual' | 'learned';
	active: boolean;
	created_at: string;
	updated_at: string;
}

/** Processing rule condition types (extended operators) */
export type ProcessingRuleOperator =
	| 'equals' | 'not_equals' | 'contains' | 'not_contains'
	| 'starts_with' | 'ends_with' | 'in' | 'not_in'
	| 'matches' | 'gt' | 'gte' | 'lt' | 'lte'
	| 'exists' | 'not_exists';

export interface ProcessingSimpleCondition {
	field: string;
	operator: ProcessingRuleOperator;
	value?: string | string[] | number | boolean | null;
}

export interface ProcessingAllCondition {
	all: ProcessingCondition[];
}

export interface ProcessingAnyCondition {
	any: ProcessingCondition[];
}

export interface ProcessingNotCondition {
	not: ProcessingCondition;
}

export type ProcessingCondition =
	| ProcessingSimpleCondition
	| ProcessingAllCondition
	| ProcessingAnyCondition
	| ProcessingNotCondition;

/** Processing rule action types */
export type ProcessingRuleAction =
	| { type: 'set_status'; status: 'dismissed' | 'archived' | 'done'; reason?: string }
	| { type: 'set_priority'; priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' }
	| { type: 'bookmark' }
	| { type: 'run_entity_agent'; prompt_template?: string }
	| { type: 'execute_egress'; platform: string; action_type: string; payload_template: Record<string, string>; connection_id?: string }
	| { type: 'send_notification'; title_template: string; body_template: string }
	| { type: 'add_tag'; tag_name: string; create_if_missing?: boolean };

/** A processing rule (automated event→action) */
export interface ProcessingRule {
	id: number;
	name: string;
	description: string | null;
	space_id: string | null;
	enabled: boolean;
	position: number;
	condition: ProcessingCondition;
	actions: ProcessingRuleAction[];
	rate_limit: number;
	cooldown_secs: number;
	max_daily: number;
	last_fired_at: string | null;
	fire_count: number;
	error_count: number;
	last_error: string | null;
	created_at: string;
	updated_at: string;
}

/** Processing rule firing history entry */
export interface ProcessingRuleFiring {
	id: number;
	card_id: string;
	entity_id: string | null;
	event_id: string | null;
	fired_at: string;
	actions: ProcessingRuleAction[];
	results: Array<{ success: boolean; error?: string }>;
	error: string | null;
}

/** Request to update a card's classification */
export interface UpdateClassificationRequest {
	priority?: string;
	persona?: string;
	rule_text?: string;
}

/** A configured repository */
export interface Repo {
	name: string;
	path: string;
	platform: string;
	remote_id: string;
}

/** repos.json structure */
export interface ReposConfig {
	repos: Repo[];
}

/** Workspace session */
export interface WorkspaceSession {
	session_id: string;
	agent_type: string;
	status: string;
	repo_path?: string;
	add_dirs?: string[];
	started_at?: string;
	updated_at?: string;
	completed_at?: string;
	findings?: Record<string, unknown>;
	error_message?: string;
	session_type?: 'code' | 'research';
	permission_mode?: string;
}

/** Workspace event */
export interface WorkspaceEvent {
	event_id: string;
	timestamp: string;
	event_type: string;
	actor: string;
	content: Record<string, unknown>;
	requires_input: boolean;
	agent_message_id?: string | null;
}

/** Workspace response from GET /cards/:card_id/workspace */
export interface WorkspaceResponse {
	card_id: string;
	session: WorkspaceSession | null;
	events: WorkspaceEvent[];
	context: Record<string, unknown>;
}

/** Model configuration from settings.json */
export interface ModelSettings {
	router: string;
	stager: string;
	chat: string;
	trace: string;
	local: string;
}

/** API key presence indicators (never exposes actual keys) */
export interface ApiKeyStatus {
	anthropic: boolean;
	openai: boolean;
	google: boolean;
	openrouter: boolean;
	n8n: boolean;
}

/** A single model option returned by the available-models endpoint */
export interface ModelOption {
	id: string;
	name: string;
}

/** A provider group in the available-models response */
export interface ProviderModels {
	provider: string;
	label: string;
	models: ModelOption[];
}

/** Response from GET /settings/available-models */
export interface AvailableModelsResponse {
	providers: ProviderModels[];
}

/** n8n integration settings */
export interface N8nSettings {
	base_url: string;
	webhooks: Record<string, string>;
}

/** n8n connection test result */
export interface N8nTestResult {
	base_url: string;
	health: string;
	webhook: {
		path: string;
		status_code?: number;
		reachable: boolean;
		error?: string;
	} | null;
}

/** Feed filter/sort preferences persisted to settings */
export interface FeedPreferences {
	statusFilters: string[];
	priorityFilters: string[];
	sortBy: string;
	showArchived: boolean;
	showBookmarked: boolean;
	spaceFilter: string[];
}

/** Pipeline processing settings (advanced) */
export interface PipelineSettings {
	model_timeout: number;
	llm_retries: number;
	max_retry_attempts: number;
	max_concurrent_events: number;
	queue_poll_interval: number;
}

/** Full settings response from GET /settings */
export interface Settings {
	models: ModelSettings;
	api_keys: ApiKeyStatus;
	coding_agent: string;
	agent_paths: Record<string, string>;
	privacy: {
		tier3_sources: string[];
		tier3_processing: string;
	};
	briefing: {
		enabled: boolean;
		time: string;
		timezone: string;
		per_space?: boolean;
	};
	notifications: {
		enabled: boolean;
		min_priority: string;
	};
	retention?: {
		card_retention_days: number;
		chat_retention_days: number;
		audit_retention_days: number;
		omni_retention_days: number;
		ingestion_errors_retention_days: number;
	};
	omni?: {
		enabled: boolean;
		resynthesis_time: string;
		density: string;
		timezone: string;
		rolling_interval_hours: number;
		event_threshold: number;
	};
	n8n?: N8nSettings;
	feed_preferences?: FeedPreferences;
	pipeline?: PipelineSettings;
	setup_complete?: boolean;
	smart_grouping?: {
		context_association: boolean;
		smart_display: boolean;
		strictness: 'strict' | 'balanced' | 'lenient' | 'custom';
		confidence_threshold: number;
		auto_confirm_threshold: number;
		centroid_threshold: number;
		cross_platform_required: boolean;
		entity_ref_overlap_mode: 'hard_gate' | 'soft_boost' | 'disabled';
		always_llm: boolean;
	};
	group_summaries?: {
		enabled: boolean;
	};
	mcp?: {
		tool_scopes: McpToolScopes;
		auth_mode: McpAuthMode;
	};
}

export type McpAuthMode = 'bearer' | 'none';

export interface McpToolScopes {
	read: boolean;
	write: boolean;
	egress: boolean;
}

export interface McpConfig {
	tool_scopes: McpToolScopes;
	auth_mode: McpAuthMode;
	has_token: boolean;
	token_prefix: string | null;
	sse_url: string;
}

export interface McpConfigUpdate {
	tool_scopes?: McpToolScopes;
	auth_mode?: McpAuthMode;
}

export interface McpToken {
	token: string;
}

/** Staged output attached to an action card */
export interface StagedOutput {
	type: 'draft_reply' | 'code_fix' | 'briefing' | 'summary' | 'agent_result' | 'agent_plan';
	content: string;
}

/** A suggested action the user can approve */
export interface SuggestedAction {
	action_id: string;
	label: string;
	action_type: string;
	target_platform: string;
	payload: Record<string, unknown>;
}

/** Full action card from the engine */
/** A tag definition */
export interface Tag {
	tag_id: number;
	name: string;
	color?: string;
	is_system: boolean;
	created_at?: string;
}

/** A tag applied to a card, entity group, or context group */
export interface TagAssignment {
	tag_id: number;
	tag_name: string;
	color?: string;
	is_system: boolean;
	assigned_by: string;
}

export interface ActionCard {
	card_id: string;
	event_id: string;
	created_at?: string;
	priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
	persona: 'ENGINEER' | 'COMMS' | 'OPS' | 'SALES' | 'HR' | 'FINANCE';
	category: string;
	header: string;
	summary: string;
	intelligence?: string[];
	staged_output?: StagedOutput;
	suggested_actions?: SuggestedAction[];
	status:
		| 'pending'
		| 'ready'
		| 'executing'
		| 'done'
		| 'failed'
		| 'dismissed'
		| 'archived'
		| 'agent_running'
		| 'awaiting_input';
	privacy_tier: number;
	has_workspace: boolean;
	resolved_at?: string;
	user_feedback?: string;
	feedback_type?: string;
	confidence?: number;
	router_model?: string;
	stager_model?: string;
	updated_at?: string;
	entity_id?: string;
	source_ref?: string;
	source_url?: string;
	selected_action_id?: string;
	actor_name?: string;
	actor_email?: string;
	space_id?: string;
	space_name?: string;
	space_color?: string;
	bookmarked_at?: string;
	read_at?: string;
	group_active_at?: string;
	context_id?: string;
	last_error?: string;
	source_context?: string;
	tags?: TagAssignment[];
}

/** A structured key event with separate timestamp */
export interface KeyEvent {
	event: string;
	timestamp?: string;
}

/** Rolling LLM-generated summary for an entity group */
export interface GroupSummary {
	entity_id: string;
	headline: string;
	summary: string;
	key_events?: (string | KeyEvent)[];
	current_status?: string;
	pending_actions?: string[];
	card_count: number;
	card_ids: string[];
	updated_at?: string;
}

/** A group of cards sharing the same entity or semantic context */
export interface CardGroup {
	entity_id: string;
	entity_title: string;
	entity_url?: string;
	platform: string;
	card_count: number;
	top_priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
	latest_at: string;
	has_pending: boolean;
	unread_count: number;
	cards: ActionCard[];
	sort_key?: string;
	context_id?: string;
	context_label?: string;
	platforms?: string[];
	group_summary?: GroupSummary;
	tags?: TagAssignment[];
	sub_groups?: CardGroup[];
}

/** Response from GET /cards/grouped */
export interface GroupedCardsResponse {
	groups: CardGroup[];
	total_groups: number;
	date?: string;
	prev_date?: string;
	next_date?: string;
	space_id?: string;
}

/** Paginated cards list from GET /cards */
export interface CardsListResponse {
	cards: ActionCard[];
	total: number;
	limit: number;
	offset: number;
}

/** A single item in a daily summary section */
export interface SummaryItem {
	text: string;
	card_id: string;
	priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
	status: 'pending' | 'done' | 'dismissed' | 'archived';
	space_id?: string;
	space_name?: string;
	space_color?: string;
}

/** Structured daily summary */
export interface DaySummary {
	events_and_meetings: SummaryItem[];
	action_items: SummaryItem[];
	key_updates: SummaryItem[];
}

/** Per-space summary entry returned by GET /summary */
export interface SpaceSummary {
	space_id: string;
	space_name: string;
	space_color: string;
	summary: DaySummary | null;
	card_ids: string[];
	updated_at: string | null;
}

/** Response from GET /summary */
export interface DaySummaryResponse {
	date: string;
	space_summaries: SpaceSummary[];
}

/** Request body for POST /actions/execute */
export interface ExecuteActionRequest {
	card_id: string;
	action_id: string;
	modifications?: Record<string, unknown>;
}

/** Response body from POST /actions/execute */
export interface ExecuteActionResponse {
	card_id: string;
	action_id: string;
	status: string;
	result_url?: string;
	error?: string;
}

/** Dashboard stat counts */
export interface DashboardStats {
	events_processed: number;
	events_filtered: number;
	cards_generated: number;
	cards_pending: number;
	cards_approved: number;
	cards_dismissed: number;
	cards_edited: number;
	actions_executed: number;
	actions_completed: number;
	actions_failed: number;
}

export interface TimeSavedEstimate {
	total_minutes: number;
	by_action_type: Record<string, number>;
}

export interface LLMCostEstimate {
	total_cost_usd: number;
	by_model: Record<string, number>;
	by_feature: Record<string, number>;
	by_step: Record<string, number>;
	total_input_tokens: number;
	total_output_tokens: number;
}

export interface SourceBreakdown {
	source: string;
	count: number;
}

export interface PersonaApprovalRate {
	persona: string;
	total: number;
	approved: number;
	dismissed: number;
	rate: number;
}

export interface ResponseTimeStats {
	avg_ms: number;
	p50_ms: number;
	p95_ms: number;
}

export interface DashboardResponse {
	stats: DashboardStats;
	time_saved: TimeSavedEstimate;
	llm_costs: LLMCostEstimate;
	events_by_source: SourceBreakdown[];
	approval_by_persona: PersonaApprovalRate[];
	response_time: ResponseTimeStats;
	period_days: number;
}

/** Chat message */
export interface ChatMessage {
	message_id: string;
	timestamp: string;
	role: 'user' | 'assistant';
	content: string;
	referenced_cards: string[];
	referenced_events: string[];
	conversation_id?: string;
}

/** Chat response from POST /chat */
export interface ChatResponse {
	message: ChatMessage;
	referenced_cards: string[];
	referenced_events: string[];
}

/** Chat conversation */
export interface Conversation {
	conversation_id: string;
	title: string;
	space_id: string | null;
	created_at: string;
	updated_at: string;
	preview: string;
	message_count: number;
}

/** Audit log entry */
export interface AuditLogEntry {
	log_id: string;
	timestamp: string;
	event_id?: string;
	card_id?: string;
	step: string;
	model_used?: string;
	input_tokens: number;
	output_tokens: number;
	latency_ms: number;
	success: boolean;
	error?: string;
}

/** Paginated audit log response */
export interface AuditLogResponse {
	entries: AuditLogEntry[];
	total: number;
	limit: number;
	offset: number;
}

/** Dead event — failed permanently after exhausting retries */
export interface DeadEvent {
	event_id: string;
	timestamp: string;
	source_platform: string;
	subject_type: string;
	subject_title: string;
	subject_url?: string;
	actor_name?: string;
	processing_attempts: number;
	manual_retries: number;
	last_error?: string;
	created_at: string;
}

/** Paginated dead events response */
export interface DeadEventsResponse {
	events: DeadEvent[];
	total: number;
	limit: number;
	offset: number;
}

/** Response from retrying dead events */
export interface RetryDeadEventsResponse {
	retried: number;
}

/** Ingestion error captured from n8n workflow failures */
export interface IngestionError {
	error_id: string;
	workflow_id: string;
	source_id?: string;
	space_id?: string;
	platform?: string;
	workflow_name?: string;
	node_name?: string;
	error_name?: string;
	error_message?: string;
	error_http_code?: number;
	occurrence_count: number;
	first_occurred_at: string;
	last_occurred_at: string;
	acknowledged_at?: string;
	resolved_at?: string;
	cleared_at?: string;
}

/** Ingestion errors list response */
export interface IngestionErrorsResponse {
	errors: IngestionError[];
}

/** Response from clearing ingestion errors */
export interface ClearIngestionErrorsResponse {
	cleared: number;
}

/** Inbound Laya Event (n8n -> Engine) */
export interface LayaEvent {
	event_id: string;
	timestamp: string;
	source: {
		platform: string;
		connection_id?: string;
		raw_event_type: string;
	};
	actor: {
		name: string;
		email: string;
		platform_handle?: string;
	};
	subject: {
		type: string;
		id: string;
		title: string;
		url?: string;
	};
	content: {
		body: string;
		attachments: string[];
		metadata: Record<string, unknown>;
	};
}

/** Field definition for a platform credential form */
export interface FieldDef {
	key: string;
	label: string;
	type: 'text' | 'password';
	placeholder?: string;
	help?: string;
}

/** Platform configuration from GET /connections/platforms */
export interface PlatformConfig {
	label: string;
	category: string;
	icon: string;
	n8n_type: string;
	n8n_node: string;
	oauth: boolean;
	fields: FieldDef[];
}

/** Response from POST /settings/n8n/bootstrap */
export interface N8nBootstrapResponse {
	status: string;
	message: string;
	has_api_key: boolean;
}

/** Platforms registry response */
export interface PlatformsResponse {
	platforms: Record<string, PlatformConfig>;
}

/** An n8n credential as returned by GET /connections */
export interface N8nConnection {
	id: string;
	name: string;
	type: string;
	platform: string | null;
	platform_label: string;
	created_at: string;
	updated_at: string;
}

/** Response from GET /connections */
export interface ConnectionsResponse {
	connections: N8nConnection[];
}

/** Request body for POST /connections */
export interface CreateConnectionRequest {
	platform: string;
	name: string;
	credentials: Record<string, string>;
}

/** Response from POST /connections */
export interface CreateConnectionResponse {
	status: string;
	id: string;
	name: string;
	platform: string;
}

/** Response from POST /connections/test */
export interface ConnectionTestResult {
	status: 'connected' | 'unauthorized' | 'unreachable' | 'timeout' | 'no_api_key' | 'error';
	message: string;
}

/** Space for organizing event sources with model/key configs */
export interface Space {
	space_id: string;
	name: string;
	description?: string;
	icon: string;
	color: string;
	router_model?: string;
	stager_model?: string;
	chat_model?: string;
	trace_model?: string;
	omni_model?: string;
	coding_agent?: string;
	is_default: boolean;
	paused: boolean;
	position: number;
	source_count: number;
	created_at?: string;
	updated_at?: string;
}

/** Source: maps an n8n workflow to a space */
export interface Source {
	source_id: string;
	name: string;
	platform: string;
	workflow_id: string;
	space_id: string;
	space_name?: string;
	source_type?: string;
	webhook_path?: string;
	created_at?: string;
}

/** Available n8n workflow for source assignment */
export interface AvailableWorkflow {
	workflow_id: string;
	name: string;
	platform: string;
	source_type: string;
	active: boolean;
	registered: boolean;
	connection_id?: string;
}

/** Response from GET /spaces */
export interface SpacesResponse {
	spaces: Space[];
}

/** Response from GET /sources */
export interface SourcesResponse {
	sources: Source[];
}

/** Response from GET /sources/available-workflows */
export interface AvailableWorkflowsResponse {
	workflows: AvailableWorkflow[];
}

/** A repo assigned to a space */
export interface SpaceRepo {
	repo_name: string;
	position: number;
	path: string | null;
	platform: string;
	remote_id: string;
	exists: boolean;
}

/** Response from GET /spaces/:id/repos */
export interface SpaceReposResponse {
	repos: SpaceRepo[];
}

/** Response from GET /spaces/:id/api-keys */
export interface SpaceApiKeysResponse {
	providers: Record<string, { configured: boolean }>;
}

/** A custom local model provider (LMStudio, Ollama, etc.) */
export interface CustomProvider {
	id: string;
	name: string;
	base_url: string;
	provider_type: 'lmstudio' | 'ollama' | 'openai_compatible';
	default_timeout: number;
	api_key_ref?: string;
	capabilities_override?: {
		supports_tool_calling?: boolean;
		supports_structured_output?: boolean;
	};
}

/** Result from testing a custom provider's connectivity */
export interface CustomProviderTestResult {
	provider_id: string;
	reachable: boolean;
	models_count: number;
	llm_count: number;
	embedding_count: number;
	inference_ok: boolean;
	latency_ms: number;
	error: string | null;
}

/** A model discovered from a custom provider */
export interface DiscoveredModel {
	id: string;
	name: string;
	type: string;
	max_context_length: number | null;
	supports_tool_calling: boolean;
	supports_structured_output: boolean;
	supports_vision: boolean;
	params: string | null;
	quantization: string | null;
	loaded: boolean;
}

// Budget / Cost Control
export interface BudgetConfig {
	monthly_limit_usd: number | null;
	enabled: boolean;
	current_month_cost: number;
	current_month: string;
	by_model: Record<string, number>;
	by_feature: Record<string, number>;
	by_step: Record<string, number>;
	total_input_tokens: number;
	total_output_tokens: number;
	is_paused: boolean;
	paused_workflow_count: number;
}

export interface MonthlyCostEntry {
	year_month: string;
	total_cost_usd: number;
	by_model: Record<string, number>;
	total_input_tokens: number;
	total_output_tokens: number;
}

// Trace (semantic cross-platform entity search)
export interface TraceEntity {
	entity_id: string;
	title: string;
	url?: string;
	platform: string;
}

export interface TraceChapter {
	label: string;
	timestamp: string;
	card_ids: string[];
}

export interface TraceStatusSummary {
	current_state: string;
	platforms_involved: string[];
	total_cards: number;
	date_range: { from: string; to: string };
	pending_actions: number;
}

export interface TraceCluster {
	cluster_id: string;
	primary_entity: TraceEntity;
	linked_entities: TraceEntity[];
	narrative?: string;
	chapters: TraceChapter[];
	timeline: ActionCard[];
	status_summary: TraceStatusSummary;
}

export interface TraceSearchMetadata {
	semantic_hits: number;
	fuzzy_hits: number;
	entity_hits: number;
	expansion_cards: number;
	elapsed_ms: number;
	fuzzy_search: boolean;
	enable_semantic?: boolean;
	enable_text?: boolean;
	enable_llm_filter?: boolean;
}

export interface TraceResponse {
	trace_id: string;
	query: string;
	clusters: TraceCluster[];
	search_metadata: TraceSearchMetadata;
	created_at: string;
	summary?: string | null;
}

export interface TraceListItem {
	trace_id: string;
	query: string;
	created_at: string;
	total_cards: number;
	platforms: string[];
	fuzzy_search: boolean;
	enable_semantic: boolean;
	enable_text: boolean;
	enable_llm_filter: boolean;
}

// ---------------------------------------------------------------------------
// Egress types
// ---------------------------------------------------------------------------

/** Request to execute an egress action */
export interface EgressExecuteRequest {
	platform: string;
	action_type: string;
	payload: Record<string, unknown>;
	connection_id?: string;
	source_card_id?: string;
	source_event_id?: string;
	space_id?: string;
}

/** Request for AI-assisted draft generation */
export interface EgressAiAssistRequest {
	platform: string;
	action_type: string;
	context: Record<string, unknown>;
}

/** Response from AI-assisted draft generation */
export interface EgressAiAssistResponse {
	draft: Record<string, string>;
}

/** Response from egress execution */
export interface EgressExecuteResponse {
	status: string;
	result_url?: string;
	result_data?: Record<string, unknown>;
}

/** Response from egress preview */
export interface EgressPreviewResponse {
	platform: string;
	action_type: string;
	summary: string;
	details: Record<string, unknown>;
	warnings: string[];
	estimated_impact: string;
}

/** A platform capability */
export interface EgressCapability {
	action_type: string;
	label: string;
	requires_fields: string[];
	optional_fields: string[];
	description: string;
	confirmation_required: boolean;
}

/** Response from capabilities endpoint */
export interface EgressCapabilitiesResponse {
	platform: string;
	capabilities: EgressCapability[];
}

export interface ComposeFieldAutocomplete {
	scope: 'all' | 'platform';
	sources: string[];
}

export interface ComposeField {
	name: string;
	required: boolean;
	type: 'text' | 'email' | 'textarea' | 'select' | 'datetime-local';
	label: string;
	placeholder: string;
	options?: string[];
	autocomplete?: ComposeFieldAutocomplete;
}

export interface ComposeAction {
	action_type: string;
	label: string;
	fields: ComposeField[];
}

export interface ComposePlatform {
	id: string;
	label: string;
	icon: string;
	actions: ComposeAction[];
}

export interface ComposePlatformsResponse {
	platforms: ComposePlatform[];
}

export interface CardEgressAction {
	action_type: string;
	label: string;
	impact: 'low' | 'medium' | 'high';
}

export interface CardEgressContext {
	platform: string;
	actions: CardEgressAction[];
	prefill: Record<string, unknown>;
	event_id: string | null;
	connected: boolean;
	connection_id: string | null;
}

/** An egress connection */
export interface EgressConnection {
	connection_id: string;
	platform: string;
	name: string;
	status: 'connected' | 'error' | 'expired';
	capabilities: string[];
	space_id?: string;
	error_message?: string;
	last_validated_at?: string;
	created_at: string;
}

/** Response from egress connections list */
export interface EgressConnectionsResponse {
	connections: EgressConnection[];
}

/** Request to create an egress connection */
export interface EgressConnectRequest {
	platform: string;
	name?: string;
	credentials: Record<string, string>;
	space_id?: string;
}

/** Response from creating an egress connection */
export interface EgressConnectResponse {
	status: string;
	connection_id?: string;
	capabilities: string[];
}

/** Email provider detection result */
export interface EmailProviderDetection {
	detected: boolean;
	provider: string;
	method: 'oauth' | 'app_password' | 'manual';
	redirect_platform?: string;
	smtp_host?: string;
	smtp_port?: number;
	imap_host?: string;
	imap_port?: number;
	use_tls?: boolean;
	note?: string;
}

/** OAuth flow start response */
export interface OAuthStartResponse {
	auth_url: string;
	state: string;
}

// ---------------------------------------------------------------------------
// Omni — rolling cross-platform summary
// ---------------------------------------------------------------------------

export interface OmniItem {
	text: string;
	source_cards: string[];
	platforms: string[];
	priority: string;
	pinned: boolean;
	bookmarked: boolean;
	space_id?: string;
}

export interface OmniSection {
	type: 'attention' | 'recent' | 'period' | 'milestone';
	label: string | null;
	items: OmniItem[];
}

export interface OmniStats {
	events_processed: number;
	cards_acted_on: number;
	compression_ratio: number;
}

export interface OmniSnapshot {
	snapshot_id: string | null;
	space_id: string;
	version: number;
	generated_at: string | null;
	snapshot_type: string | null;
	sections: OmniSection[];
	stats: OmniStats;
	card_ids: string[];
}

export interface OmniHistoryEntry {
	snapshot_id: string;
	version: number;
	generated_at: string;
	snapshot_type: string;
	events_processed: number;
}

export interface OmniHistoryResponse {
	space_id: string;
	snapshots: OmniHistoryEntry[];
}

export interface TimelineEntry {
	snapshot_id: string;
	version: number;
	generated_at: string;
	snapshot_type: string;
	events_processed: number;
}

export type TimelineTier = 'today' | 'this_week' | 'earlier';

export interface TimelineSegment {
	tier: TimelineTier;
	label: string;
	range_start: string | null;
	range_end: string | null;
	entries: TimelineEntry[];
}

export interface OmniTimelineResponse {
	space_id: string;
	segments: TimelineSegment[];
}

export interface OmniPin {
	pin_id: string;
	space_id: string;
	item_text: string;
	source_card_ids: string[];
	platforms: string[];
	pinned_at: string;
}

export interface OmniPinsResponse {
	space_id: string;
	pins: OmniPin[];
}

/** WebSocket open_compose event data */
export interface OpenComposeEvent {
	type: 'open_compose';
	platform: string;
	action_type: 'reply' | 'compose' | 'comment' | 'forward';
	prefill: Record<string, unknown>;
	source_card_id?: string;
}
