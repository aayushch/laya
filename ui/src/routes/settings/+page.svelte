<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
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
	import { getEngineUrl } from '$lib/config';
	import KeybindingsConfig from '$lib/components/settings/KeybindingsConfig.svelte';
	import DataConfig from '$lib/components/settings/DataConfig.svelte';
	import BriefingConfig from '$lib/components/settings/BriefingConfig.svelte';
	import MCPConfig from '$lib/components/settings/MCPConfig.svelte';
	import AboutConfig from '$lib/components/settings/AboutConfig.svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { fade } from 'svelte/transition';

	type TabId = 'team' | 'rules' | 'models' | 'repos' | 'agent' | 'integrations' | 'spaces' | 'scheduling' | 'mcp' | 'audit' | 'appearance' | 'keybindings' | 'data' | 'about';
	const validTabs = new Set<string>(['team', 'rules', 'models', 'repos', 'agent', 'integrations', 'spaces', 'scheduling', 'mcp', 'audit', 'appearance', 'keybindings', 'data', 'about']);
	const SETTINGS_TAB_KEY = 'laya-settings-tab';
	let activeTab = $state<TabId>('team');

	// React to URL query params (works for both initial load and client-side navigation)
	$effect(() => {
		const tab = page.url.searchParams.get('tab');
		const section = page.url.searchParams.get('section');

		if (tab && validTabs.has(tab)) {
			activeTab = tab as TabId;
		} else if (!tab) {
			// No tab in URL — restore from localStorage
			const saved = localStorage.getItem(SETTINGS_TAB_KEY);
			if (saved && validTabs.has(saved)) {
				activeTab = saved as TabId;
			}
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

	function switchTab(tabId: TabId) {
		activeTab = tabId;
		localStorage.setItem(SETTINGS_TAB_KEY, tabId);
		// Update URL without full navigation so back button works
		goto(`/settings?tab=${tabId}`, { replaceState: true, noScroll: true });
	}
	const tabs = [
		{ id: 'team',    label: 'Team' },
		{ id: 'rules',   label: 'Rules' },
		{ id: 'models',  label: 'Models' },
		{ id: 'repos',   label: 'Repos' },
		{ id: 'agent',   label: 'Agent' },
		{ id: 'integrations', label: 'Integrations' },
		{ id: 'spaces',       label: 'Spaces' },
		{ id: 'scheduling',   label: 'Features' },
		{ id: 'mcp',          label: 'MCP' },
		{ id: 'audit',        label: 'Audit' },
		{ id: 'appearance',   label: 'Appearance' },
		{ id: 'keybindings',  label: 'Keys' },
		{ id: 'data',         label: 'Data' },
		{ id: 'about',        label: 'About' }
	];

	// Responsive tab bar: keep as many tabs inline as fit, collapse the rest into an
	// overflow "More" menu when the bar narrows (same principle as the feed toolbar).
	let tabBarEl = $state<HTMLElement>();
	let measureEl = $state<HTMLElement>();
	let visibleCount = $state(tabs.length);
	let overflowMenuOpen = $state(false);
	let moreBtnRef = $state<HTMLElement | null>(null);
	let overflowPanelRef = $state<HTMLDivElement | null>(null);
	let overflowPos = $state({ top: 0, right: 0 });

	function positionOverflow() {
		if (!moreBtnRef) return;
		const r = moreBtnRef.getBoundingClientRect();
		overflowPos = { top: r.bottom + 4, right: window.innerWidth - r.right };
	}
	const visibleTabs = $derived(tabs.slice(0, visibleCount));
	const overflowTabs = $derived(tabs.slice(visibleCount));

	$effect(() => {
		if (!tabBarEl || !measureEl) return;
		const recompute = () => {
			const inner = tabBarEl!.clientWidth - 8; // subtract p-1 (4px each side)
			const widths = (Array.from(measureEl!.children) as HTMLElement[]).map((el) => el.offsetWidth);
			const gap = 4; // gap-1
			const total = widths.reduce((a, b) => a + b, 0) + gap * Math.max(0, widths.length - 1);
			if (total <= inner) {
				visibleCount = tabs.length;
				return;
			}
			// Reserve room for the "More" button when at least one tab overflows.
			const moreReserve = 70;
			let used = 0;
			let count = 0;
			for (let i = 0; i < widths.length; i++) {
				const add = widths[i] + (i > 0 ? gap : 0);
				if (used + add + gap + moreReserve <= inner) {
					used += add;
					count++;
				} else break;
			}
			visibleCount = Math.max(1, count);
		};
		const observer = new ResizeObserver(recompute);
		observer.observe(tabBarEl);
		recompute();
		return () => observer.disconnect();
	});

	$effect(() => {
		function onClick(e: MouseEvent) {
			const target = e.target as Node;
			if (moreBtnRef?.contains(target)) return;
			if (overflowPanelRef?.contains(target)) return;
			overflowMenuOpen = false;
		}
		document.addEventListener('click', onClick);
		return () => document.removeEventListener('click', onClick);
	});

	let exporting = $state(false);

	async function exportDiagnostics() {
		exporting = true;
		try {
			const resp = await fetch(`${getEngineUrl()}/diagnostics/export`);
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

<div class="overflow-x-clip">
<div class="mx-auto max-w-5xl">
	<div class="sticky -top-4 z-20 relative space-y-4 pb-4 pt-4 before:absolute before:inset-y-0 before:-left-[50vw] before:-right-[50vw] before:z-[-1] {$glassTheme ? 'before:backdrop-blur-xl' : 'before:bg-surface-900'}">
		<div>
			<h2 class="text-laya-heading font-semibold">Settings</h2>
			<p class="text-laya-base text-surface-400">Manage your team, rules, models, repos, and coding agent</p>
		</div>

		<!-- Tab bar -->
		<div
			bind:this={tabBarEl}
			class="relative flex items-center gap-1 rounded-lg p-1 {overflowTabs.length === 0 ? 'justify-between' : ''} {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-800'}"
		>
			{#each visibleTabs as tab}
				<button
					class="whitespace-nowrap rounded-md px-2.5 py-1.5 text-laya-base font-medium transition-colors
						{activeTab === tab.id
							? 'bg-laya-orange/15 text-laya-orange'
							: 'text-surface-400 hover:text-surface-200'}"
					onclick={() => switchTab(tab.id as TabId)}
				>
					{tab.label}
				</button>
			{/each}

			{#if overflowTabs.length > 0}
				<div class="ml-auto">
					<button
						bind:this={moreBtnRef}
						class="flex items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-1.5 text-laya-base font-medium transition-colors
							{overflowTabs.some((t) => t.id === activeTab)
								? 'bg-laya-orange/15 text-laya-orange'
								: 'text-surface-400 hover:text-surface-200'}"
						onclick={() => { positionOverflow(); overflowMenuOpen = !overflowMenuOpen; }}
						aria-label="More tabs"
					>
						More
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
						</svg>
					</button>
				</div>
			{/if}
			{#if overflowMenuOpen}
				<div
					use:portal
					bind:this={overflowPanelRef}
					class="settings-overflow-panel fixed z-[100] flex min-w-[160px] flex-col rounded-lg border py-1 shadow-lg
						{$glassTheme
							? 'glass-dropdown border-white/15'
							: 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
					style="top: {overflowPos.top}px; right: {overflowPos.right}px;"
				>
					{#each overflowTabs as tab}
						<button
							class="w-full whitespace-nowrap px-4 py-1.5 text-left text-laya-base font-medium transition-colors
								{$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}
								{activeTab === tab.id ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={() => { switchTab(tab.id as TabId); overflowMenuOpen = false; }}
						>
							{tab.label}
						</button>
					{/each}
				</div>
			{/if}

			<!-- Hidden full-width measurement row: mirrors button sizing so we can compute how many tabs fit -->
			<div bind:this={measureEl} aria-hidden="true" class="pointer-events-none invisible absolute left-1 top-0 flex items-center gap-1">
				{#each tabs as tab}
					<span class="whitespace-nowrap rounded-md px-2.5 py-1.5 text-laya-base font-medium">{tab.label}</span>
				{/each}
			</div>
		</div>
	</div>

	<div class="space-y-6">

	{#key activeTab}
	<div in:fade={{ duration: $reducedMotion ? 0 : 150 }}>
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
	{:else if activeTab === 'scheduling'}
		<BriefingConfig />
	{:else if activeTab === 'mcp'}
		<MCPConfig />
	{:else if activeTab === 'appearance'}
		<AppearanceConfig />
	{:else if activeTab === 'keybindings'}
		<KeybindingsConfig />
	{:else if activeTab === 'data'}
		<DataConfig />
	{:else if activeTab === 'about'}
		<AboutConfig />
	{:else}
		<AuditLogViewer />
	{/if}
	</div>
	{/key}

	<!-- Diagnostics footer -->
	<div class="flex items-center justify-between border-t border-surface-700/50 pt-4 mt-2">
		<span class="text-laya-secondary text-surface-500">Need help troubleshooting? Export diagnostics for support.</span>
		<button
			class="rounded-md border border-surface-600 px-3 py-1 text-laya-secondary text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-200 disabled:opacity-50"
			onclick={exportDiagnostics}
			disabled={exporting}
		>
			{exporting ? 'Exporting...' : 'Export Diagnostics'}
		</button>
	</div>
	</div>
</div>
</div>
