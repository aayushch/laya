import type {
	TeamConfig,
	RulesConfig,
	Settings,
	ReposConfig,
	ActionCard,
	CardsListResponse
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
	approveCard: (cardId: string, modifications?: Record<string, unknown>) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/approve`, {
			method: 'POST',
			body: JSON.stringify({ modifications: modifications ?? null })
		}),
	dismissCard: (cardId: string, reason?: string, feedbackType?: string) =>
		request<{ status: string; card_id: string }>(`/cards/${cardId}/dismiss`, {
			method: 'POST',
			body: JSON.stringify({ reason: reason ?? null, feedback_type: feedbackType ?? null })
		})
};
