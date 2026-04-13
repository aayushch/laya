import type {
	TeamConfig,
	RulesConfig,
	Settings,
	ReposConfig,
	ActionCard,
	CardsListResponse,
	GroupedCardsResponse,
	ExecuteActionResponse,
	WorkspaceResponse,
	DashboardResponse,
	ChatMessage,
	ChatResponse,
	Conversation,
	AuditLogResponse,
	N8nTestResult,
	PlatformsResponse,
	ConnectionsResponse,
	CreateConnectionRequest,
	CreateConnectionResponse,
	ConnectionTestResult,
	N8nBootstrapResponse,
	DaySummaryResponse,
	SpacesResponse,
	SourcesResponse,
	AvailableWorkflowsResponse,
	Space,
	Source,
	SpaceApiKeysResponse,
	SpaceReposResponse,
	AvailableModelsResponse,
	CustomProvider,
	CustomProviderTestResult,
	DiscoveredModel,
	BudgetConfig,
	MonthlyCostEntry,
	EgressExecuteRequest,
	EgressExecuteResponse,
	EgressPreviewResponse,
	EgressCapabilitiesResponse,
	EgressConnectionsResponse,
	EgressConnectRequest,
	EgressConnectResponse,
	EgressAiAssistRequest,
	EgressAiAssistResponse,
	EmailProviderDetection,
	OAuthStartResponse,
	OmniSnapshot,
	OmniHistoryResponse,
	OmniTimelineResponse,
	OmniPinsResponse,
	OmniPin,
	DeadEventsResponse,
	RetryDeadEventsResponse
} from './types';

const ENGINE_URL = 'http://127.0.0.1:8420';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
	const resp = await fetch(`${ENGINE_URL}${path}`, {
		headers: { 'Content-Type': 'application/json' },
		...options
	});
	if (!resp.ok) {
		let detail: string | undefined;
		try {
			const body = await resp.json();
			detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
		} catch {
			// no parseable body
		}
		throw new Error(detail || `Engine API error: ${resp.status} ${resp.statusText}`);
	}
	return resp.json();
}

export const engineApi = {
	// Team
	getTeam: () => request<TeamConfig>('/team'),
	updateTeam: (team: TeamConfig) =>
		request<{ status: string }>('/team', {
			method: 'PUT',
			body: JSON.stringify(team)
		}),

	// Rules
	getRules: () => request<RulesConfig>('/rules'),
	updateRules: (rules: RulesConfig) =>
		request<{ status: string }>('/rules', {
			method: 'PUT',
			body: JSON.stringify(rules)
		}),

	// Repos
	getRepos: () => request<ReposConfig>('/repos'),
	updateRepos: (repos: ReposConfig) =>
		request<{ status: string }>('/repos', {
			method: 'PUT',
			body: JSON.stringify(repos)
		}),

	// Settings
	getSettings: () => request<Settings>('/settings'),
	updateSettings: (settings: Partial<Settings>) =>
		request<{ status: string }>('/settings', {
			method: 'PUT',
			body: JSON.stringify(settings)
		}),

	detectAgentPaths: () =>
		request<{ agent_paths: Record<string, string> }>('/settings/detect-agents'),

	// API Keys
	setApiKey: (provider: string, apiKey: string) =>
		request<{ status: string; provider: string }>('/settings/api-key', {
			method: 'PUT',
			body: JSON.stringify({ provider, api_key: apiKey })
		}),
	deleteApiKey: (provider: string) =>
		request<{ status: string; provider: string }>(`/settings/api-key/${provider}`, {
			method: 'DELETE'
		}),

	// Available models (dynamic, grouped by provider)
	getAvailableModels: (refresh?: boolean) =>
		request<AvailableModelsResponse>(
			`/settings/available-models${refresh ? '?refresh=true' : ''}`
		),

	// Cards
	getCards: (params?: {
		status?: string;
		priority?: string;
		limit?: number;
		offset?: number;
		sort?: string;
	}) => {
		const searchParams = new URLSearchParams();
		if (params?.status) searchParams.set('status', params.status);
		if (params?.priority) searchParams.set('priority', params.priority);
		if (params?.limit) searchParams.set('limit', String(params.limit));
		if (params?.offset) searchParams.set('offset', String(params.offset));
		if (params?.sort) searchParams.set('sort', params.sort);
		const qs = searchParams.toString();
		return request<CardsListResponse>(`/cards${qs ? '?' + qs : ''}`);
	},
	getCard: (cardId: string) => request<ActionCard>(`/cards/${cardId}`),
	getGroupedCards: (params?: {
		status?: string;
		priority?: string;
		sort?: string;
		sort_asc?: boolean;
		show_archived?: boolean;
		date?: string;
		space_id?: string;
		bookmarked?: boolean;
	}) => {
		const searchParams = new URLSearchParams();
		if (params?.status) searchParams.set('status', params.status);
		if (params?.priority) searchParams.set('priority', params.priority);
		if (params?.sort) searchParams.set('sort', params.sort);
		if (params?.sort_asc) searchParams.set('sort_asc', 'true');
		if (params?.show_archived) searchParams.set('show_archived', 'true');
		if (params?.date) searchParams.set('date', params.date);
		if (params?.space_id) searchParams.set('space_id', params.space_id);
		if (params?.bookmarked) searchParams.set('bookmarked', 'true');
		searchParams.set('tz', Intl.DateTimeFormat().resolvedOptions().timeZone);
		const qs = searchParams.toString();
		return request<GroupedCardsResponse>(`/cards/grouped${qs ? '?' + qs : ''}`);
	},
	dismissGroup: (entityId: string) =>
		request<{ dismissed: number; entity_id: string }>(
			`/cards/group/${encodeURIComponent(entityId)}/dismiss-all`,
			{ method: 'POST' }
		),
	markCardDone: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/done`, {
			method: 'POST'
		}),
	approveAgent: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/approve-agent`, {
			method: 'POST'
		}),
	runAgent: (data: {
		prompt: string;
		directory: string;
		add_dirs?: string[];
		agent_type?: string;
		mode?: string;
		space_id?: string;
		images?: string[];
	}) =>
		request<{ status: string; card_id: string }>('/cards/run-agent', {
			method: 'POST',
			body: JSON.stringify(data)
		}),
	startResearch: (cardId: string, data?: { prompt?: string; directory?: string }) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/start-research`, {
			method: 'POST',
			body: JSON.stringify(data ?? {})
		}),
	dismissCard: (cardId: string, reason?: string, feedbackType?: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/dismiss`, {
			method: 'POST',
			body: JSON.stringify({ reason: reason ?? null, feedback_type: feedbackType ?? null })
		}),
	archiveCard: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/archive`, {
			method: 'POST'
		}),
	reopenCard: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/reopen`, {
			method: 'POST'
		}),
	// Context group management
	getContextGroup: (contextId: string) =>
		request<{ context_id: string; label: string; user_confirmed: boolean; user_split: boolean; members: Array<{ entity_id: string; confidence: number; link_method: string }>; cards: Array<{ card_id: string; header: string; entity_id: string; status: string }> }>(`/cards/groups/${contextId}`),
	unlinkContextGroup: (contextId: string) =>
		request<{ status: string; context_id: string }>(`/cards/groups/${contextId}/unlink`, {
			method: 'POST'
		}),
	mergeCards: (cardIds: string[]) =>
		request<{ status: string; context_id: string; card_count: number }>('/cards/groups/merge', {
			method: 'POST',
			body: JSON.stringify({ card_ids: cardIds })
		}),
	deleteCard: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}`, {
			method: 'DELETE'
		}),
	bookmarkCard: (cardId: string) =>
		request<{ status: string; card_id: string; bookmarked_at: string }>(`/cards/${cardId}/bookmark`, {
			method: 'POST'
		}),
	unbookmarkCard: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/unbookmark`, {
			method: 'POST'
		}),
	updateCardClassification: (cardId: string, body: import('./types').UpdateClassificationRequest) =>
		request<{ status: string; card_id: string; corrections: number }>(`/cards/${cardId}/classification`, {
			method: 'PATCH',
			body: JSON.stringify(body)
		}),

	// Classification rules
	getClassificationRules: (spaceId?: string) => {
		const qs = spaceId ? `?space_id=${spaceId}` : '';
		return request<import('./types').ClassificationRule[]>(`/classification/rules${qs}`);
	},
	createClassificationRule: (body: { rule_text: string; field?: string | null; space_id?: string | null }) =>
		request<{ id: number; status: string }>('/classification/rules', {
			method: 'POST',
			body: JSON.stringify(body)
		}),
	updateClassificationRule: (ruleId: number, body: { rule_text?: string; field?: string | null; active?: boolean }) =>
		request<{ id: number; status: string }>(`/classification/rules/${ruleId}`, {
			method: 'PUT',
			body: JSON.stringify(body)
		}),
	deleteClassificationRule: (ruleId: number) =>
		request<{ id: number; status: string }>(`/classification/rules/${ruleId}`, {
			method: 'DELETE'
		}),

	// Summary
	getDaySummary: (date?: string) => {
		const params = new URLSearchParams();
		if (date) params.set('date', date);
		params.set('tz', Intl.DateTimeFormat().resolvedOptions().timeZone);
		return request<DaySummaryResponse>(`/summary?${params.toString()}`);
	},

	// Actions
	executeAction: (cardId: string, actionId: string, modifications?: Record<string, unknown>) =>
		request<ExecuteActionResponse>('/actions/execute', {
			method: 'POST',
			body: JSON.stringify({
				card_id: cardId,
				action_id: actionId,
				modifications: modifications ?? null
			})
		}),

	updateActionPayload: (cardId: string, actionId: string, payload: Record<string, string>) =>
		request<{ status: string }>(`/cards/${cardId}/action-payload`, {
			method: 'POST',
			body: JSON.stringify({ action_id: actionId, payload })
		}),

	// Workspace
	getWorkspace: (cardId: string) => request<WorkspaceResponse>(`/cards/${cardId}/workspace`),

	answerAgentQuestion: (sessionId: string, answers: Array<{ header?: string; selected: string }>, addDirs?: string[]) =>
		request<{ status: string; session_id: string }>(`/workspace/${sessionId}/answer`, {
			method: 'POST',
			body: JSON.stringify({ answers, add_dirs: addDirs?.length ? addDirs : undefined })
		}),

	resumeSession: (sessionId: string, prompt: string, addDirs?: string[]) =>
		request<{ status: string; session_id: string }>(`/workspace/${sessionId}/resume`, {
			method: 'POST',
			body: JSON.stringify({ prompt, add_dirs: addDirs?.length ? addDirs : undefined })
		}),

	dismissQuestions: (sessionId: string) =>
		request<{ status: string; session_id: string }>(`/workspace/${sessionId}/dismiss-questions`, {
			method: 'POST'
		}),

	// Research file browsing
	listResearchFiles: (cardId: string) =>
		request<{ card_id: string; files: Array<{ name: string; path: string; size: number; modified: number }> }>(
			`/workspace/research-files/${cardId}`
		),
	readResearchFile: (cardId: string, filePath: string) =>
		request<{ path: string; name: string; content: string }>(
			`/workspace/research-files/${cardId}/read?path=${encodeURIComponent(filePath)}`
		),

	// Dashboard
	getDashboard: (days?: number) => {
		const qs = days ? `?days=${days}` : '';
		return request<DashboardResponse>(`/dashboard${qs}`);
	},

	// Chat
	getChatHistory: (limit?: number, conversationId?: string) => {
		const params = new URLSearchParams();
		if (limit) params.set('limit', String(limit));
		if (conversationId) params.set('conversation_id', conversationId);
		const qs = params.toString();
		return request<ChatMessage[]>(`/chat/history${qs ? '?' + qs : ''}`);
	},
	sendChat: (message: string, conversationId?: string, cardContext?: string) =>
		request<ChatResponse>('/chat', {
			method: 'POST',
			body: JSON.stringify({
				message,
				conversation_id: conversationId ?? null,
				...(cardContext ? { card_context: cardContext } : {})
			})
		}),

	// Chat Conversations
	getConversations: (limit?: number) => {
		const qs = limit ? `?limit=${limit}` : '';
		return request<Conversation[]>(`/chat/conversations${qs}`);
	},
	createConversation: (title?: string, spaceId?: string) =>
		request<Conversation>('/chat/conversations', {
			method: 'POST',
			body: JSON.stringify({ title: title ?? 'New Chat', space_id: spaceId ?? null })
		}),
	getConversationMessages: (conversationId: string, limit?: number) => {
		const qs = limit ? `?limit=${limit}` : '';
		return request<ChatMessage[]>(`/chat/conversations/${conversationId}/messages${qs}`);
	},
	deleteConversation: (conversationId: string) =>
		request<{ status: string; conversation_id: string }>(`/chat/conversations/${conversationId}`, {
			method: 'DELETE'
		}),
	renameConversation: (conversationId: string, title: string) =>
		request<{ status: string; conversation_id: string }>(`/chat/conversations/${conversationId}`, {
			method: 'PUT',
			body: JSON.stringify({ title })
		}),

	// n8n
	testN8nConnection: (baseUrl?: string, webhookPath?: string) =>
		request<N8nTestResult>('/settings/n8n/test', {
			method: 'POST',
			body: JSON.stringify({
				...(baseUrl ? { base_url: baseUrl } : {}),
				...(webhookPath ? { webhook_path: webhookPath } : {})
			})
		}),

	// Connections (n8n credentials)
	getPlatforms: () => request<PlatformsResponse>('/connections/platforms'),

	getConnections: () => request<ConnectionsResponse>('/connections'),

	createConnection: (req: CreateConnectionRequest) =>
		request<CreateConnectionResponse>('/connections', {
			method: 'POST',
			body: JSON.stringify(req)
		}),

	deleteConnection: (id: string) =>
		request<{ status: string; id: string }>(`/connections/${id}`, {
			method: 'DELETE'
		}),

	testN8nApi: () =>
		request<ConnectionTestResult>('/connections/test', {
			method: 'POST'
		}),

	bootstrapN8n: () =>
		request<N8nBootstrapResponse>('/settings/n8n/bootstrap', {
			method: 'POST'
		}),

	// Spaces
	getSpaces: () => request<SpacesResponse>('/spaces'),

	createSpace: (space: { name: string; description?: string; icon?: string; color?: string; router_model?: string; stager_model?: string; chat_model?: string; coding_agent?: string }) =>
		request<Space>('/spaces', {
			method: 'POST',
			body: JSON.stringify(space)
		}),

	updateSpace: (spaceId: string, updates: Partial<{ name: string; description: string; icon: string; color: string; router_model: string; stager_model: string; chat_model: string; coding_agent: string }>) =>
		request<{ status: string; space_id: string }>(`/spaces/${spaceId}`, {
			method: 'PUT',
			body: JSON.stringify(updates)
		}),

	deleteSpace: (spaceId: string) =>
		request<{ status: string; space_id: string }>(`/spaces/${spaceId}`, {
			method: 'DELETE'
		}),

	// Space API Keys
	getSpaceApiKeys: (spaceId: string) =>
		request<SpaceApiKeysResponse>(`/spaces/${spaceId}/api-keys`),

	setSpaceApiKey: (spaceId: string, provider: string, apiKey: string) =>
		request<{ status: string; provider: string }>(`/spaces/${spaceId}/api-key`, {
			method: 'PUT',
			body: JSON.stringify({ provider, api_key: apiKey })
		}),

	deleteSpaceApiKey: (spaceId: string, provider: string) =>
		request<{ status: string; provider: string }>(`/spaces/${spaceId}/api-key/${provider}`, {
			method: 'DELETE'
		}),

	// Space Pause / Unpause
	setSpacePaused: (spaceId: string, paused: boolean) =>
		request<{ status: string; space_id: string; workflows_toggled: number; errors: Array<{ workflow_id: string; name: string; error?: string; issues?: string[] }> }>(`/spaces/${spaceId}/paused`, {
			method: 'PUT',
			body: JSON.stringify({ paused })
		}),

	// Space Repos
	getSpaceRepos: (spaceId: string) =>
		request<SpaceReposResponse>(`/spaces/${spaceId}/repos`),

	setSpaceRepos: (spaceId: string, repoNames: string[]) =>
		request<{ status: string; count: number }>(`/spaces/${spaceId}/repos`, {
			method: 'PUT',
			body: JSON.stringify({ repo_names: repoNames })
		}),

	// Sources
	getSources: () => request<SourcesResponse>('/sources'),

	getAvailableWorkflows: () => request<AvailableWorkflowsResponse>('/sources/available-workflows'),

	setWorkflowActive: (workflowId: string, active: boolean) =>
		request<{ status: string; workflow_id: string; active: boolean }>(`/sources/workflows/${workflowId}/active`, {
			method: 'PUT',
			body: JSON.stringify({ active })
		}),

	createSource: (source: { name: string; platform: string; workflow_id: string; space_id?: string; source_type?: string; webhook_path?: string }) =>
		request<Source>('/sources', {
			method: 'POST',
			body: JSON.stringify(source)
		}),

	reassignSource: (sourceId: string, spaceId: string) =>
		request<{ status: string; source_id: string; space_id: string }>(`/sources/${sourceId}/space`, {
			method: 'PUT',
			body: JSON.stringify({ space_id: spaceId })
		}),

	deleteSource: (sourceId: string) =>
		request<{ status: string; source_id: string }>(`/sources/${sourceId}`, {
			method: 'DELETE'
		}),

	bulkAssignSources: (spaceId: string, sourceIds: string[]) =>
		request<{ status: string; updated: number }>(`/spaces/${spaceId}/sources`, {
			method: 'PUT',
			body: JSON.stringify({ source_ids: sourceIds })
		}),

	// Custom Providers (local models)
	getCustomProviders: () =>
		request<{ providers: CustomProvider[] }>('/settings/custom-providers'),

	addCustomProvider: (provider: {
		name: string;
		base_url: string;
		provider_type: string;
		api_key?: string;
		default_timeout?: number;
		capabilities_override?: { supports_tool_calling?: boolean; supports_structured_output?: boolean };
	}) =>
		request<{ status: string; provider: CustomProvider }>('/settings/custom-providers', {
			method: 'POST',
			body: JSON.stringify(provider)
		}),

	updateCustomProvider: (providerId: string, updates: {
		name?: string;
		base_url?: string;
		provider_type?: string;
		api_key?: string;
		default_timeout?: number;
		capabilities_override?: { supports_tool_calling?: boolean; supports_structured_output?: boolean };
	}) =>
		request<{ status: string; provider: CustomProvider }>(`/settings/custom-providers/${providerId}`, {
			method: 'PUT',
			body: JSON.stringify(updates)
		}),

	deleteCustomProvider: (providerId: string) =>
		request<{ status: string; provider_id: string }>(`/settings/custom-providers/${providerId}`, {
			method: 'DELETE'
		}),

	testCustomProvider: (providerId: string) =>
		request<CustomProviderTestResult>(`/settings/custom-providers/${providerId}/test`, {
			method: 'POST'
		}),

	getProviderModels: (providerId: string) =>
		request<{ provider_id: string; provider_name: string; models: DiscoveredModel[] }>(
			`/settings/custom-providers/${providerId}/models`
		),

	// Budget / Cost Control
	getBudget: () => request<BudgetConfig>('/budget'),

	updateBudget: (config: { monthly_limit_usd: number | null; enabled: boolean }) =>
		request<{ status: string; monthly_limit_usd: number | null; enabled: boolean }>('/budget', {
			method: 'PUT',
			body: JSON.stringify(config)
		}),

	getBudgetHistory: (months?: number) =>
		request<{ months: MonthlyCostEntry[] }>(`/budget/history${months ? '?months=' + months : ''}`),

	resumeBudget: () =>
		request<{ status: string; resumed_count: number; errors: Array<{ workflow_id: string; error?: string; issues?: string[] }> }>('/budget/resume', {
			method: 'POST'
		}),

	// Trace
	runTrace: (query: string, spaceId?: string, fuzzySearch = false, opts?: {
		enableSemantic?: boolean;
		enableText?: boolean;
		enableLlmFilter?: boolean;
	}) =>
		request<import('./types').TraceResponse>('/trace', {
			method: 'POST',
			body: JSON.stringify({
				query,
				space_id: spaceId || null,
				include_archived: true,
				max_results: 50,
				fuzzy_search: fuzzySearch,
				...(opts?.enableSemantic !== undefined && { enable_semantic: opts.enableSemantic }),
				...(opts?.enableText !== undefined && { enable_text: opts.enableText }),
				...(opts?.enableLlmFilter !== undefined && { enable_llm_filter: opts.enableLlmFilter }),
			})
		}),

	getTraces: (limit = 20, offset = 0) =>
		request<import('./types').TraceListItem[]>(`/traces?limit=${limit}&offset=${offset}`),

	cancelTrace: () =>
		request<{ cancelled: string[] }>('/trace/cancel', { method: 'POST' }),

	getTrace: (traceId: string) =>
		request<import('./types').TraceResponse>(`/traces/${traceId}`),

	rerunTrace: (traceId: string) =>
		request<import('./types').TraceResponse>(`/traces/${traceId}/rerun`, { method: 'POST' }),

	deleteTrace: (traceId: string) =>
		request<{ deleted: string }>(`/traces/${traceId}`, { method: 'DELETE' }),

	generateClusterNarrative: (traceId: string, clusterId: string) =>
		request<{ status: string }>(`/traces/${traceId}/clusters/${clusterId}/narrative`, {
			method: 'POST'
		}),

	generateTraceSummary: (traceId: string) =>
		request<{ status: string }>(`/traces/${traceId}/summary`, {
			method: 'POST'
		}),

	removeCluster: (traceId: string, clusterId: string) =>
		request<{ removed: string }>(`/traces/${traceId}/clusters/${clusterId}`, {
			method: 'DELETE'
		}),

	restoreClusters: (traceId: string) =>
		request<{ restored: string }>(`/traces/${traceId}/clusters/restore`, {
			method: 'POST'
		}),

	exportTrace: async (traceId: string): Promise<Blob> => {
		const resp = await fetch(`${ENGINE_URL}/traces/${traceId}/export`);
		if (!resp.ok) throw new Error(`Export failed: ${resp.status}`);
		return resp.blob();
	},

	// Audit Log
	getAuditLog: (params?: {
		step?: string;
		success?: boolean;
		limit?: number;
		offset?: number;
	}) => {
		const searchParams = new URLSearchParams();
		if (params?.step) searchParams.set('step', params.step);
		if (params?.success !== undefined) searchParams.set('success', String(params.success));
		if (params?.limit) searchParams.set('limit', String(params.limit));
		if (params?.offset) searchParams.set('offset', String(params.offset));
		const qs = searchParams.toString();
		return request<AuditLogResponse>(`/audit-log${qs ? '?' + qs : ''}`);
	},

	// Dead Events
	getDeadEvents: (params?: { limit?: number; offset?: number }) => {
		const searchParams = new URLSearchParams();
		if (params?.limit) searchParams.set('limit', String(params.limit));
		if (params?.offset) searchParams.set('offset', String(params.offset));
		const qs = searchParams.toString();
		return request<DeadEventsResponse>(`/events/dead${qs ? '?' + qs : ''}`);
	},

	retryDeadEvents: (eventIds?: string[]) =>
		request<RetryDeadEventsResponse>('/events/dead/retry', {
			method: 'POST',
			body: JSON.stringify(eventIds ? { event_ids: eventIds } : { all: true })
		}),

	// Egress
	egressExecute: (data: EgressExecuteRequest) =>
		request<EgressExecuteResponse>('/egress/execute', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	egressPreview: (data: EgressExecuteRequest) =>
		request<EgressPreviewResponse>('/egress/preview', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	getEgressCapabilities: (platform: string) =>
		request<EgressCapabilitiesResponse>(`/egress/capabilities/${platform}`),

	listEgressConnections: () => request<EgressConnectionsResponse>('/egress/connections'),

	egressAiAssist: (data: EgressAiAssistRequest) =>
		request<EgressAiAssistResponse>('/egress/ai-assist', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	getConnectionNames: (platform: string) =>
		request<{ names: string[] }>(`/egress/connections/names/${platform}`),

	createEgressConnection: (data: EgressConnectRequest) =>
		request<EgressConnectResponse>('/egress/connections', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	deleteEgressConnection: (connectionId: string) =>
		request<{ status: string }>(`/egress/connections/${connectionId}`, {
			method: 'DELETE'
		}),

	testEgressConnection: (connectionId: string) =>
		request<{ connection_id: string; valid: boolean; error?: string }>(
			`/egress/connections/test/${connectionId}`,
			{ method: 'POST' }
		),

	detectEmailProvider: (email: string) =>
		request<EmailProviderDetection>(`/egress/connections/detect?email=${encodeURIComponent(email)}`),

	startOAuthFlow: (platform: string, connectionName?: string) => {
		const params = new URLSearchParams({ platform });
		if (connectionName) params.set('connection_name', connectionName);
		return request<OAuthStartResponse>(`/egress/connections/oauth/start?${params}`);
	},

	setupOAuthClient: (data: { platform: string; client_id: string; client_secret: string }) =>
		request<{ status: string; platform: string }>('/egress/connections/oauth/setup', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	// Omni — rolling cross-platform summary
	getOmni: (spaceId = 'default', version?: number) => {
		const params = new URLSearchParams({ space_id: spaceId });
		if (version !== undefined) params.set('version', String(version));
		return request<OmniSnapshot>(`/omni?${params}`);
	},

	getOmniHistory: (spaceId = 'default', limit = 30) =>
		request<OmniHistoryResponse>(`/omni/history?space_id=${encodeURIComponent(spaceId)}&limit=${limit}`),

	getOmniTimeline: (spaceId = 'default') =>
		request<OmniTimelineResponse>(`/omni/timeline?space_id=${encodeURIComponent(spaceId)}`),

	triggerOmniResynthesis: (spaceId = 'default') =>
		request<{ status: string; snapshot_ids: string[]; space_id: string }>(`/omni/resynthesis?space_id=${encodeURIComponent(spaceId)}`, {
			method: 'POST'
		}),

	getOmniPins: (spaceId = 'default') =>
		request<OmniPinsResponse>(`/omni/pins?space_id=${encodeURIComponent(spaceId)}`),

	pinOmniItem: (data: { space_id: string; text: string; source_cards: string[]; platforms: string[] }) =>
		request<OmniPin>('/omni/pin', {
			method: 'POST',
			body: JSON.stringify(data)
		}),

	unpinOmniItem: (pinId: string) =>
		request<{ status: string; pin_id: string }>(`/omni/pin/${encodeURIComponent(pinId)}`, {
			method: 'DELETE'
		}),

	toggleOmniBookmark: (data: { space_id: string; source_card_id: string; bookmarked: boolean }) =>
		request<{ status: string; bookmarked: boolean }>('/omni/bookmark', {
			method: 'POST',
			body: JSON.stringify(data)
		})
};
