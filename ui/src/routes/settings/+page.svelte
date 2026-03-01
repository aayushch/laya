<script lang="ts">
	import TeamEditor from '$lib/components/settings/TeamEditor.svelte';
	import RulesEditor from '$lib/components/settings/RulesEditor.svelte';
	import ModelConfig from '$lib/components/settings/ModelConfig.svelte';
	import RepoConfig from '$lib/components/settings/RepoConfig.svelte';
	import AgentConfig from '$lib/components/settings/AgentConfig.svelte';
	import IntegrationsConfig from '$lib/components/settings/IntegrationsConfig.svelte';
	import AuditLogViewer from '$lib/components/settings/AuditLogViewer.svelte';
	import AppearanceConfig from '$lib/components/settings/AppearanceConfig.svelte';

	let activeTab = $state<'team' | 'rules' | 'models' | 'repos' | 'agent' | 'integrations' | 'audit' | 'appearance'>('team');
	let exporting = $state(false);

	async function exportDiagnostics() {
		exporting = true;
		try {
			const resp = await fetch('http://127.0.0.1:8420/diagnostics/export');
			if (!resp.ok) throw new Error(`Export failed: ${resp.status}`);
			const blob = await resp.blob();
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = 'laya-diagnostics.zip';
			a.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			console.error('Diagnostics export failed:', e);
		} finally {
			exporting = false;
		}
	}
</script>

<div class="mx-auto max-w-4xl space-y-6">
	<div>
		<h2 class="text-2xl font-semibold">Settings</h2>
		<p class="text-sm text-surface-400">Manage your team, rules, models, repos, and coding agent</p>
	</div>

	<!-- Tab bar -->
	<div class="flex flex-wrap gap-1 rounded-lg border border-surface-700 bg-surface-800 p-1">
		{#each [
			{ id: 'team',         label: 'Team' },
			{ id: 'rules',        label: 'Rules' },
			{ id: 'models',       label: 'Models' },
			{ id: 'repos',        label: 'Repos' },
			{ id: 'agent',        label: 'Agent' },
			{ id: 'integrations', label: 'Integrations' },
			{ id: 'audit',        label: 'Audit' },
			{ id: 'appearance',   label: 'Appearance' }
		] as tab}
			<button
				class="rounded-md px-4 py-2 text-sm font-medium transition-colors
					{activeTab === tab.id
						? 'bg-laya-orange/15 text-laya-orange'
						: 'text-surface-400 hover:text-surface-200'}"
				onclick={() => (activeTab = tab.id as typeof activeTab)}
			>
				{tab.label}
			</button>
		{/each}
	</div>

	{#if activeTab === 'team'}
		<TeamEditor />
	{:else if activeTab === 'rules'}
		<RulesEditor />
	{:else if activeTab === 'models'}
		<ModelConfig />
	{:else if activeTab === 'repos'}
		<RepoConfig />
	{:else if activeTab === 'agent'}
		<AgentConfig />
	{:else if activeTab === 'integrations'}
		<IntegrationsConfig />
	{:else if activeTab === 'appearance'}
		<AppearanceConfig />
	{:else}
		<AuditLogViewer />
	{/if}

	<!-- Diagnostics -->
	<div class="rounded-lg border border-surface-700 bg-surface-800 p-4">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="font-medium">Diagnostics</h3>
				<p class="text-sm text-surface-400">Export system info, config, logs, and DB stats for troubleshooting</p>
			</div>
			<button
				class="rounded-md bg-surface-600 px-4 py-2 text-sm font-medium text-surface-50 transition-colors hover:bg-surface-500 disabled:opacity-50"
				onclick={exportDiagnostics}
				disabled={exporting}
			>
				{exporting ? 'Exporting...' : 'Export ZIP'}
			</button>
		</div>
	</div>
</div>
