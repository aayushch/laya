/** Health check response from GET /health */
export interface HealthResponse {
	engine: string;
	sqlite: string;
	n8n: string;
	uptime_seconds: number;
}

/** WebSocket message from the engine */
export interface WsMessage {
	type: string;
	event_id?: string;
	card_id?: string;
	session_id?: string;
	payload: Record<string, unknown>;
}

/** Team member from team.json */
export interface TeamMember {
	name: string;
	email: string;
	role: 'manager' | 'teammate' | 'external' | 'bot';
	notes: string;
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
	action: 'drop';
}

/** rules.json structure */
export interface RulesConfig {
	rules: Rule[];
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
	spaceFilter: string | null;
}

/** Full settings response from GET /settings */
export interface Settings {
	models: ModelSettings;
	api_keys: ApiKeyStatus;
	coding_agent: string;
	privacy: {
		tier3_sources: string[];
		tier3_processing: string;
	};
	briefing: {
		enabled: boolean;
		time: string;
		timezone: string;
	};
	notifications: {
		enabled: boolean;
		min_priority: string;
	};
	n8n?: N8nSettings;
	feed_preferences?: FeedPreferences;
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
export interface ActionCard {
	card_id: string;
	event_id: string;
	created_at?: string;
	priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
	persona: 'ENGINEER' | 'COMMS' | 'OPS';
	category: string;
	header: string;
	summary: string;
	intelligence?: string[];
	staged_output?: StagedOutput;
	suggested_actions?: SuggestedAction[];
	status:
		| 'pending'
		| 'approved'
		| 'executing'
		| 'completed'
		| 'failed'
		| 'dismissed'
		| 'archived'
		| 'agent_running'
		| 'awaiting_input'
		| 'staged';
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
}

/** A group of cards sharing the same entity (e.g. one Jira ticket) */
export interface CardGroup {
	entity_id: string;
	entity_title: string;
	entity_url?: string;
	platform: string;
	card_count: number;
	top_priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
	latest_at: string;
	has_pending: boolean;
	cards: ActionCard[];
	sort_key?: string;
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

/** Response from GET /summary */
export interface DaySummaryResponse {
	date: string;
	summary: DaySummary | null;
	card_ids: string[];
	updated_at: string | null;
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
}

/** Chat response from POST /chat */
export interface ChatResponse {
	message: ChatMessage;
	referenced_cards: string[];
	referenced_events: string[];
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
	coding_agent?: string;
	is_default: boolean;
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
