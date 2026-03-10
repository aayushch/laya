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
	AuditLogResponse,
	N8nTestResult,
	PlatformsResponse,
	ConnectionsResponse,
	CreateConnectionRequest,
	CreateConnectionResponse,
	ConnectionTestResult,
	N8nBootstrapResponse,
	DaySummaryResponse
} from './types';

const ENGINE_URL = 'http://127.0.0.1:8420';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
	const resp = await fetch(`${ENGINE_URL}${path}`, {
		headers: { 'Content-Type': 'application/json' },
		...options
	});
	if (!resp.ok) {
		throw new Error(`Engine API error: ${resp.status} ${resp.statusText}`);
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
		show_archived?: boolean;
		date?: string;
	}) => {
		const searchParams = new URLSearchParams();
		if (params?.status) searchParams.set('status', params.status);
		if (params?.priority) searchParams.set('priority', params.priority);
		if (params?.sort) searchParams.set('sort', params.sort);
		if (params?.show_archived) searchParams.set('show_archived', 'true');
		if (params?.date) searchParams.set('date', params.date);
		const qs = searchParams.toString();
		return request<GroupedCardsResponse>(`/cards/grouped${qs ? '?' + qs : ''}`);
	},
	dismissGroup: (entityId: string) =>
		request<{ dismissed: number; entity_id: string }>(
			`/cards/group/${encodeURIComponent(entityId)}/dismiss-all`,
			{ method: 'POST' }
		),
	approveCard: (cardId: string, modifications?: Record<string, unknown>) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/approve`, {
			method: 'POST',
			body: JSON.stringify({ modifications: modifications ?? null })
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
	deleteCard: (cardId: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}`, {
			method: 'DELETE'
		}),

	// Summary
	getDaySummary: (date?: string) => {
		const qs = date ? `?date=${date}` : '';
		return request<DaySummaryResponse>(`/summary${qs}`);
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

	// Dashboard
	getDashboard: (days?: number) => {
		const qs = days ? `?days=${days}` : '';
		return request<DashboardResponse>(`/dashboard${qs}`);
	},

	// Chat
	getChatHistory: (limit?: number) => {
		const qs = limit ? `?limit=${limit}` : '';
		return request<ChatMessage[]>(`/chat/history${qs}`);
	},
	sendChat: (message: string) =>
		request<ChatResponse>('/chat', {
			method: 'POST',
			body: JSON.stringify({ message })
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
	}
};
