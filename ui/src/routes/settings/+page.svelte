<script lang="ts">
	import TeamEditor from '$lib/components/settings/TeamEditor.svelte';
	import RulesEditor from '$lib/components/settings/RulesEditor.svelte';
	import ModelConfig from '$lib/components/settings/ModelConfig.svelte';
	import RepoConfig from '$lib/components/settings/RepoConfig.svelte';
	import AgentConfig from '$lib/components/settings/AgentConfig.svelte';
	import IntegrationsConfig from '$lib/components/settings/IntegrationsConfig.svelte';
	import SpacesConfig from '$lib/components/settings/SpacesConfig.svelte';
	import AuditLogViewer from '$lib/components/settings/AuditLogViewer.svelte';
	import AppearanceConfig from '$lib/components/settings/AppearanceConfig.svelte';
	import DataConfig from '$lib/components/settings/DataConfig.svelte';
	import BriefingConfig from '$lib/components/settings/BriefingConfig.svelte';
	import { page } from '$app/state';

	type TabId = 'team' | 'rules' | 'models' | 'repos' | 'agent' | 'integrations' | 'spaces' | 'briefing' | 'audit' | 'appearance' | 'data';
	const validTabs = new Set<string>(['team', 'rules', 'models', 'repos', 'agent', 'integrations', 'spaces', 'briefing', 'audit', 'appearance', 'data']);
	let activeTab = $state<TabId>('team');

	// React to URL query params (works for both initial load and client-side navigation)
	$effect(() => {
		const tab = page.url.searchParams.get('tab');
		const section = page.url.searchParams.get('section');

		if (tab && validTabs.has(tab)) {
			activeTab = tab as TabId;
		}

		if (section) {
			// Wait two frames: one for the tab switch to render, one for layout
			requestAnimationFrame(() => {
				requestAnimationFrame(() => {
					const el = document.getElementById(section);
					if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
				});
			});
		}
	});
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
	<div class="flex gap-0.5 rounded-lg border border-surface-700 bg-surface-800 p-1">
		{#each [
			{ id: 'team',         label: 'Team' },
			{ id: 'rules',        label: 'Rules' },
			{ id: 'models',       label: 'Models' },
			{ id: 'repos',        label: 'Repos' },
			{ id: 'agent',        label: 'Agent' },
			{ id: 'integrations', label: 'Integrations' },
			{ id: 'spaces',       label: 'Spaces' },
			{ id: 'briefing',     label: 'Briefing' },
			{ id: 'audit',        label: 'Audit' },
			{ id: 'appearance',   label: 'Appearance' },
			{ id: 'data',         label: 'Data' }
		] as tab}
			<button
				class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors
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
	{:else if activeTab === 'spaces'}
		<SpacesConfig />
	{:else if activeTab === 'briefing'}
		<BriefingConfig />
	{:else if activeTab === 'appearance'}
		<AppearanceConfig />
	{:else if activeTab === 'data'}
		<DataConfig />
	{:else}
		<AuditLogViewer />
	{/if}

	<!-- Diagnostics footer -->
	<div class="flex items-center justify-between border-t border-surface-700/50 pt-4 mt-2">
		<span class="text-xs text-surface-500">Need help troubleshooting? Export diagnostics for support.</span>
		<button
			class="rounded-md border border-surface-600 px-3 py-1 text-xs text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-200 disabled:opacity-50"
			onclick={exportDiagnostics}
			disabled={exporting}
		>
			{exporting ? 'Exporting...' : 'Export Diagnostics'}
		</button>
	</div>
</div>
