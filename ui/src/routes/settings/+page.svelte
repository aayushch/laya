<script lang="ts">
	import TeamEditor from '$lib/components/settings/TeamEditor.svelte';
	import RulesEditor from '$lib/components/settings/RulesEditor.svelte';
	import ModelConfig from '$lib/components/settings/ModelConfig.svelte';
	import RepoConfig from '$lib/components/settings/RepoConfig.svelte';
	import AgentConfig from '$lib/components/settings/AgentConfig.svelte';

	let activeTab = $state<'team' | 'rules' | 'models' | 'repos' | 'agent'>('team');
</script>

<div class="mx-auto max-w-4xl space-y-6">
	<div>
		<h2 class="text-2xl font-semibold">Settings</h2>
		<p class="text-sm text-surface-400">Manage your team, rules, models, repos, and coding agent</p>
	</div>

	<!-- Tab bar -->
	<div class="flex gap-1 rounded-lg border border-surface-700 bg-surface-800 p-1">
		<button
			class="rounded-md px-4 py-2 text-sm font-medium transition-colors
				{activeTab === 'team' ? 'bg-surface-600 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'team')}
		>
			Team
		</button>
		<button
			class="rounded-md px-4 py-2 text-sm font-medium transition-colors
				{activeTab === 'rules' ? 'bg-surface-600 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'rules')}
		>
			Rules
		</button>
		<button
			class="rounded-md px-4 py-2 text-sm font-medium transition-colors
				{activeTab === 'models' ? 'bg-surface-600 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'models')}
		>
			Models
		</button>
		<button
			class="rounded-md px-4 py-2 text-sm font-medium transition-colors
				{activeTab === 'repos' ? 'bg-surface-600 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'repos')}
		>
			Repos
		</button>
		<button
			class="rounded-md px-4 py-2 text-sm font-medium transition-colors
				{activeTab === 'agent' ? 'bg-surface-600 text-surface-50' : 'text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'agent')}
		>
			Agent
		</button>
	</div>

	{#if activeTab === 'team'}
		<TeamEditor />
	{:else if activeTab === 'rules'}
		<RulesEditor />
	{:else if activeTab === 'models'}
		<ModelConfig />
	{:else if activeTab === 'repos'}
		<RepoConfig />
	{:else}
		<AgentConfig />
	{/if}
</div>
