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
