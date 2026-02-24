import type { TeamConfig, RulesConfig, Settings, ReposConfig } from './types';

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
		})
};
