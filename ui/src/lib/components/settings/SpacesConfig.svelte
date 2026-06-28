<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { slide } from 'svelte/transition';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import type { Space, Source, AvailableWorkflow, Repo, ProviderModels, AgentBackend } from '$lib/api/types';
	import { CODING_AGENTS } from '$lib/config';
	import ModelSelect from './ModelSelect.svelte';

	const providers = [
		{ id: 'anthropic', label: 'Anthropic' },
		{ id: 'openai', label: 'OpenAI' },
		{ id: 'google', label: 'Google' },
		{ id: 'openrouter', label: 'OpenRouter' }
	];

	let availableModels = $state<ProviderModels[]>([]);

	// Derived from the global CODING_AGENTS list so every supported agent (e.g. Pi)
	// appears automatically — a hardcoded copy here drifted out of sync and dropped Pi.
	// '' = use the global default; 'none' is excluded since a space override must name a real agent.
	const agentOptions = [
		{ value: '', label: 'Use default' },
		...CODING_AGENTS.filter((a) => a.value !== 'none').map((a) => ({ value: a.value, label: a.label }))
	];

	function integrationDisplayName(name: string): string {
		return name.replace(/ \((Ingestion|Executor)\)$/i, '');
	}

	const platformIcons: Record<string, string> = {
		github: '⚙',
		gmail: '✉',
		jira: '🎫',
		slack: '💬',
		bitbucket: '🔧',
		calendar: '📅',
		unknown: '❓'
	};

	const presetColors = [
		'#F97316', '#EAB308', '#22C55E', '#06B6D4',
		'#3B82F6', '#8B5CF6', '#EC4899', '#EF4444'
	];

	// State
	let spaces = $state<Space[]>([]);
	let sources = $state<Source[]>([]);
	let workflows = $state<AvailableWorkflow[]>([]);
	let loaded = $state(false);

	// Form state — used for both create (top-level) and inline edit
	let editingSpaceId = $state<string | null>(null);
	let showCreateForm = $state(false);
	let formName = $state('');
	let formDescription = $state('');
	let formColor = $state('#3B82F6');
	let formRouterModel = $state('');
	let formStagerModel = $state('');
	let formChatModel = $state('');
	let formTraceModel = $state('');
	let formOmniModel = $state('');
	let formCodingAgent = $state('');
	let saving = $state(false);

	// --- Per-space agent inference backend ---
	// Same model as the global Models tab: structured roles (router/stager/omni) can run on
	// an installed CLI agent (stored as `agent/<id>/<model>`); chat/coherence stay on a model
	// provider. The coding-agent field above is a separate axis (engineer tasks), untouched.
	const AGENT_ROLES = ['router', 'stager', 'omni'] as const;
	const AGENT_LABELS: Record<string, string> = Object.fromEntries(
		CODING_AGENTS.filter((a) => a.value !== 'none').map((a) => [a.value, a.label])
	);
	const AGENT_MODEL_PH: Record<string, string> = {
		claude_code: 'claude-sonnet-4-6', codex_cli: 'gpt-5-codex',
		gemini_cli: 'gemini-2.5-pro', pi_cli: 'lmstudio/qwen3.6-35b-a3b'
	};
	let agentBackends = $state<AgentBackend[]>([]);
	let formAgentMode = $state(false);
	let formSelectedAgent = $state('claude_code');
	let formProviderBackup = $state<Record<string, string>>({});

	const formModelAccessors: Record<string, { get: () => string; set: (v: string) => void }> = {
		router: { get: () => formRouterModel, set: (v) => (formRouterModel = v) },
		stager: { get: () => formStagerModel, set: (v) => (formStagerModel = v) },
		omni: { get: () => formOmniModel, set: (v) => (formOmniModel = v) }
	};

	function parseAgentModel(v: string): { agentId: string; modelString: string } | null {
		if (!v || !v.startsWith('agent/')) return null;
		const parts = v.split('/');
		return { agentId: parts[1] || '', modelString: parts.slice(2).join('/') };
	}

	function formAgentModelString(role: string): string {
		const p = parseAgentModel(formModelAccessors[role].get());
		return p ? p.modelString : '';
	}

	function inferFormAgentMode() {
		const inferred = AGENT_ROLES.map((r) => parseAgentModel(formModelAccessors[r].get())).find(
			(p) => p
		);
		formAgentMode = !!inferred;
		formSelectedAgent = inferred?.agentId || 'claude_code';
		formProviderBackup = {};
	}

	function setFormAgentMode(on: boolean) {
		if (on === formAgentMode) return;
		formAgentMode = on;
		for (const r of AGENT_ROLES) {
			const acc = formModelAccessors[r];
			if (on) {
				if (!parseAgentModel(acc.get())) {
					formProviderBackup[r] = acc.get();
					acc.set(`agent/${formSelectedAgent}`);
				}
			} else if (parseAgentModel(acc.get())) {
				acc.set(formProviderBackup[r] ?? '');
			}
		}
	}

	function selectFormAgent(agentId: string) {
		formSelectedAgent = agentId;
		for (const r of AGENT_ROLES) {
			const ms = formAgentModelString(r);
			formModelAccessors[r].set(ms ? `agent/${agentId}/${ms}` : `agent/${agentId}`);
		}
	}

	function handleFormAgentInput(role: string) {
		return (e: Event) => {
			const typed = (e.target as HTMLInputElement).value.trim();
			formModelAccessors[role].set(
				typed ? `agent/${formSelectedAgent}/${typed}` : `agent/${formSelectedAgent}`
			);
		};
	}

	// Source assignment state
	let assigningSpaceId = $state<string | null>(null);
	let selectedWorkflows = $state<Set<string>>(new Set());
	let workflowSearch = $state('');

	// Space API key state
	let keySpaceId = $state<string | null>(null);
	let spaceKeyStatus = $state<Record<string, boolean>>({});
	let spaceKeyInputs = $state<Record<string, string>>({});
	let savingSpaceKey = $state<string | null>(null);

	// Repo assignment state
	let allRepos = $state<Repo[]>([]);
	let spaceRepoNames = $state<Record<string, string[]>>({});

	// Pause state
	let togglingPause = $state<string | null>(null);

	// Expanded space detail
	let expandedSpaceId = $state<string | null>(null);

	onMount(loadData);

	async function loadData() {
		try {
			const [spacesRes, sourcesRes, reposRes, modelsRes] = await Promise.all([
				engineApi.getSpaces(),
				engineApi.getSources(),
				engineApi.getRepos(),
				engineApi.getAvailableModels()
			]);
			spaces = spacesRes.spaces;
			sources = sourcesRes.sources;
			allRepos = reposRes.repos;
			availableModels = modelsRes.providers;
			loaded = true;

			try {
				const backendsRes = await engineApi.getAgentBackends();
				agentBackends = backendsRes.backends;
			} catch (e) {
				console.error('Failed to load agent backends:', e);
			}

			// Load repo assignments for all spaces
			for (const s of spaces) {
				loadSpaceRepos(s.space_id);
			}
		} catch (e) {
			console.error('Failed to load spaces:', e);
		}
	}

	async function loadWorkflows() {
		try {
			const res = await engineApi.getAvailableWorkflows();
			workflows = res.workflows;
		} catch (e) {
			console.error('Failed to load workflows:', e);
		}
	}

	// Space CRUD
	function startCreate() {
		showCreateForm = true;
		editingSpaceId = null;
		formName = '';
		formDescription = '';
		formColor = '#3B82F6';
		formRouterModel = '';
		formStagerModel = '';
		formChatModel = '';
		formTraceModel = '';
		formOmniModel = '';
		formCodingAgent = '';
		inferFormAgentMode();
	}

	function startEdit(space: Space) {
		showCreateForm = false;
		editingSpaceId = space.space_id;
		expandedSpaceId = space.space_id;
		formName = space.name;
		formDescription = space.description || '';
		formColor = space.color;
		formRouterModel = space.router_model || '';
		formStagerModel = space.stager_model || '';
		formChatModel = space.chat_model || '';
		formTraceModel = space.trace_model || '';
		formOmniModel = space.omni_model || '';
		formCodingAgent = space.coding_agent || '';
		inferFormAgentMode();
	}

	function cancelForm() {
		showCreateForm = false;
		editingSpaceId = null;
	}

	async function saveSpace() {
		if (!formName.trim()) return;
		saving = true;
		try {
			if (editingSpaceId) {
				await engineApi.updateSpace(editingSpaceId, {
					name: formName.trim(),
					description: formDescription.trim() || undefined,
					color: formColor,
					// null (not undefined) so the field is present in the request body and the
					// backend clears the override back to NULL — picking "Use default" emits ''.
					router_model: formRouterModel || null,
					stager_model: formStagerModel || null,
					chat_model: formChatModel || null,
					trace_model: formTraceModel || null,
					omni_model: formOmniModel || null,
					coding_agent: formCodingAgent || null
				});
			} else {
				await engineApi.createSpace({
					name: formName.trim(),
					description: formDescription.trim() || undefined,
					color: formColor,
					router_model: formRouterModel || undefined,
					stager_model: formStagerModel || undefined,
					chat_model: formChatModel || undefined,
					trace_model: formTraceModel || undefined,
					omni_model: formOmniModel || undefined,
					coding_agent: formCodingAgent || undefined
				});
			}
			cancelForm();
			await loadData();
		} catch (e) {
			console.error('Failed to save space:', e);
		} finally {
			saving = false;
		}
	}

	async function deleteSpace(space: Space) {
		if (!confirm(`Delete "${space.name}"? Sources and cards will be moved to Default.`)) return;
		try {
			await engineApi.deleteSpace(space.space_id);
			expandedSpaceId = null;
			editingSpaceId = null;
			await loadData();
		} catch (e) {
			console.error('Failed to delete space:', e);
		}
	}

	// Pause / Unpause
	async function togglePause(space: Space) {
		const newPaused = !space.paused;
		togglingPause = space.space_id;
		try {
			const res = await engineApi.setSpacePaused(space.space_id, newPaused);
			if (res.errors.length > 0) {
				const names = res.errors.map((e) => e.name).join(', ');
				console.warn(`Some workflows could not be toggled: ${names}`);
			}
			await loadData();
		} catch (e) {
			console.error('Failed to toggle pause:', e);
		} finally {
			togglingPause = null;
		}
	}

	// Filtered workflows for search
	// Group workflows by connection_id — show one entry per integration
	const filteredWorkflows = $derived.by(() => {
		const q = workflowSearch.toLowerCase().trim();
		const all = q
			? workflows.filter(wf =>
				wf.name.toLowerCase().includes(q) || wf.platform.toLowerCase().includes(q)
			)
			: [...workflows];

		// Group by connection_id: merge ingestion+executor into one entry
		const seenConnections = new Set<string>();
		const grouped: (typeof workflows[0] & { workflow_ids: string[] })[] = [];

		for (const wf of all) {
			const connId = wf.connection_id;
			if (connId) {
				if (seenConnections.has(connId)) continue;
				seenConnections.add(connId);
				// Collect all workflow IDs for this connection
				const pairIds = all.filter(w => w.connection_id === connId).map(w => w.workflow_id);
				const displayName = integrationDisplayName(wf.name);
				grouped.push({ ...wf, name: displayName, workflow_ids: pairIds });
			} else {
				grouped.push({ ...wf, workflow_ids: [wf.workflow_id] });
			}
		}

		return grouped.sort((a, b) => a.name.localeCompare(b.name));
	});

	// Source assignment
	async function startAssigning(spaceId: string) {
		assigningSpaceId = spaceId;
		selectedWorkflows = new Set();
		workflowSearch = '';
		await loadWorkflows();
	}

	function cancelAssigning() {
		assigningSpaceId = null;
		selectedWorkflows = new Set();
		workflowSearch = '';
	}

	function toggleWorkflow(allIds: string[]) {
		const next = new Set(selectedWorkflows);
		const primary = allIds[0];
		if (next.has(primary)) {
			for (const id of allIds) next.delete(id);
		} else {
			for (const id of allIds) next.add(id);
		}
		selectedWorkflows = next;
	}

	async function assignSelected() {
		if (!assigningSpaceId || selectedWorkflows.size === 0) return;
		saving = true;
		try {
			// Create sources for unregistered workflows, then reassign
			for (const wfId of selectedWorkflows) {
				const wf = workflows.find((w) => w.workflow_id === wfId);
				if (!wf) continue;

				if (!wf.registered) {
					await engineApi.createSource({
						name: wf.name.replace(/^Laya\s*-\s*/i, '').trim(),
						platform: wf.platform,
						workflow_id: wf.workflow_id,
						space_id: assigningSpaceId!
					});
				} else {
					// Find existing source and reassign
					const src = sources.find((s) => s.workflow_id === wfId);
					if (src) {
						await engineApi.reassignSource(src.source_id, assigningSpaceId!);
					}
				}
			}
			cancelAssigning();
			await loadData();
		} catch (e) {
			console.error('Failed to assign sources:', e);
		} finally {
			saving = false;
		}
	}

	async function reassignSource(sourceId: string, newSpaceId: string) {
		try {
			await engineApi.reassignSource(sourceId, newSpaceId);
			await loadData();
		} catch (e) {
			console.error('Failed to reassign source:', e);
		}
	}

	async function removeSource(sourceId: string) {
		try {
			await engineApi.deleteSource(sourceId);
			await loadData();
		} catch (e) {
			console.error('Failed to remove source:', e);
		}
	}

	// Space API keys
	async function openSpaceKeys(spaceId: string) {
		keySpaceId = spaceId;
		spaceKeyInputs = { anthropic: '', openai: '', google: '', openrouter: '' };
		try {
			const res = await engineApi.getSpaceApiKeys(spaceId);
			spaceKeyStatus = {};
			for (const [prov, info] of Object.entries(res.providers)) {
				spaceKeyStatus[prov] = info.configured;
			}
		} catch (e) {
			console.error('Failed to load space API keys:', e);
			spaceKeyStatus = {};
		}
	}

	async function saveSpaceKey(provider: string) {
		if (!keySpaceId || !spaceKeyInputs[provider]?.trim()) return;
		savingSpaceKey = provider;
		try {
			await engineApi.setSpaceApiKey(keySpaceId, provider, spaceKeyInputs[provider].trim());
			spaceKeyStatus[provider] = true;
			spaceKeyInputs[provider] = '';
		} catch (e) {
			console.error('Failed to save space API key:', e);
		} finally {
			savingSpaceKey = null;
		}
	}

	async function removeSpaceKey(provider: string) {
		if (!keySpaceId) return;
		try {
			await engineApi.deleteSpaceApiKey(keySpaceId, provider);
			spaceKeyStatus[provider] = false;
		} catch (e) {
			console.error('Failed to remove space API key:', e);
		}
	}

	// Space repos
	async function loadSpaceRepos(spaceId: string) {
		try {
			const res = await engineApi.getSpaceRepos(spaceId);
			spaceRepoNames[spaceId] = res.repos.map((r) => r.repo_name);
		} catch (e) {
			console.error('Failed to load space repos:', e);
			spaceRepoNames[spaceId] = [];
		}
	}

	function toggleSpaceRepo(spaceId: string, repoName: string) {
		const current = spaceRepoNames[spaceId] || [];
		if (current.includes(repoName)) {
			spaceRepoNames[spaceId] = current.filter((n) => n !== repoName);
		} else {
			spaceRepoNames[spaceId] = [...current, repoName];
		}
		spaceRepoNames = { ...spaceRepoNames };
	}

	async function saveSpaceRepos(spaceId: string) {
		try {
			await engineApi.setSpaceRepos(spaceId, spaceRepoNames[spaceId] || []);
		} catch (e) {
			console.error('Failed to save space repos:', e);
		}
	}

	// Helpers
	interface SourceGroup {
		displayName: string;
		platform: string;
		sources: Source[];
	}

	function sourcesForSpace(spaceId: string): Source[] {
		return sources.filter((s) => s.space_id === spaceId);
	}

	function groupedSourcesForSpace(spaceId: string): SourceGroup[] {
		const spaceSrcs = sources.filter((s) => s.space_id === spaceId);
		const groups = new Map<string, SourceGroup>();

		for (const src of spaceSrcs) {
			const displayName = integrationDisplayName(src.name);
			const key = `${src.platform}::${displayName}`;

			if (!groups.has(key)) {
				groups.set(key, { displayName, platform: src.platform, sources: [] });
			}
			groups.get(key)!.sources.push(src);
		}

		return Array.from(groups.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
	}

	async function reassignSourceGroup(group: SourceGroup, newSpaceId: string) {
		try {
			for (const src of group.sources) {
				await engineApi.reassignSource(src.source_id, newSpaceId);
			}
			await loadData();
		} catch (e) {
			console.error('Failed to reassign sources:', e);
		}
	}

	async function removeSourceGroup(group: SourceGroup) {
		try {
			for (const src of group.sources) {
				await engineApi.deleteSource(src.source_id);
			}
			await loadData();
		} catch (e) {
			console.error('Failed to remove sources:', e);
		}
	}

	function toggleExpand(spaceId: string) {
		if (expandedSpaceId === spaceId) {
			expandedSpaceId = null;
			editingSpaceId = null;
		} else {
			expandedSpaceId = spaceId;
			editingSpaceId = null;
			if (!(spaceId in spaceRepoNames)) {
				loadSpaceRepos(spaceId);
			}
		}
		keySpaceId = null;
	}

	function modelLabel(value: string | undefined | null): string {
		if (!value) return 'Default';
		for (const p of availableModels) {
			const m = p.models.find((m) => m.id === value);
			if (m) return m.name;
		}
		return value;
	}

	function agentLabel(value: string | undefined | null): string {
		if (!value) return 'Default';
		const a = agentOptions.find((a) => a.value === value);
		return a ? a.label : value;
	}
</script>

{#if !loaded}
	<div class="flex items-center justify-center py-12 text-surface-400">Loading spaces...</div>
{:else}
	<div class="space-y-6">
		<!-- Header -->
		<div class="flex items-center justify-between">
			<div>
				<h3 class="text-laya-heading font-medium">Spaces</h3>
				<p class="text-laya-base text-surface-400">
					Group event sources and assign specific models or API keys per space.
				</p>
			</div>
			{#if !showCreateForm}
				<button
					onclick={startCreate}
					class="rounded-md bg-laya-orange/15 px-4 py-2 text-laya-base font-medium text-laya-orange transition-colors hover:bg-laya-orange/25"
				>
					+ New Space
				</button>
			{/if}
		</div>

		<!-- Create Form (top-level, only for new spaces) -->
		{#if showCreateForm}
			<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-laya-orange/30 bg-surface-800'} p-5">
				<h4 class="mb-4 font-medium">New Space</h4>

				{@render spaceForm(false)}
			</div>
		{/if}

		<!-- Spaces List -->
		{#each spaces as space (space.space_id)}
			{@const spaceSources = sourcesForSpace(space.space_id)}
			{@const groupedSources = groupedSourcesForSpace(space.space_id)}
			{@const isEditing = editingSpaceId === space.space_id}
			{@const isExpanded = expandedSpaceId === space.space_id}
			<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border bg-surface-800'} transition-colors
				{!$glassTheme && (space.paused ? 'border-laya-amber/30 border-dashed' : isEditing ? 'border-laya-orange/30' : isExpanded ? 'border-laya-orange/30' : 'border-surface-700')}">
				<!-- Space Header -->
				<button
					onclick={() => toggleExpand(space.space_id)}
					class="flex w-full items-center gap-3 p-4 text-left"
				>
					<div
						class="h-3 w-3 rounded-full shrink-0"
						style="background-color: {space.color}"
					></div>
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2">
							<span class="text-laya-base font-semibold text-surface-100">{space.name}</span>
							{#if space.is_default}
								<span class="rounded {$glassTheme ? 'bg-white/[0.08]' : 'bg-surface-600'} px-1.5 py-0.5 text-laya-secondary font-medium text-surface-400">DEFAULT</span>
							{/if}
							{#if space.paused}
								<span class="rounded bg-laya-amber/20 px-1.5 py-0.5 text-laya-secondary font-semibold text-laya-amber">PAUSED</span>
							{/if}
							{#if spaceSources.length > 0}
								<!-- svelte-ignore a11y_click_events_have_key_events -->
								<!-- svelte-ignore a11y_no_static_element_interactions -->
								<span
									onclick={(e) => { e.stopPropagation(); togglePause(space); }}
									class="rounded-md px-2 py-0.5 text-laya-secondary font-medium cursor-pointer transition-colors
										{space.paused
											? 'bg-laya-orange/15 text-laya-orange hover:bg-laya-orange/25'
											: $glassTheme ? 'bg-white/[0.08] text-surface-400 hover:bg-white/[0.14] hover:text-surface-200' : 'bg-surface-700 text-surface-400 hover:bg-surface-600 hover:text-surface-200'}
										{togglingPause === space.space_id ? ' opacity-50 pointer-events-none' : ''}"
									title={space.paused ? 'Resume all ingestion workflows' : 'Pause all ingestion workflows'}
								>
									{#if togglingPause === space.space_id}
										...
									{:else if space.paused}
										▶ Resume
									{:else}
										⏸ Pause
									{/if}
								</span>
							{/if}
						</div>
						{#if space.description}
							<p class="truncate text-laya-base text-surface-500">{space.description}</p>
						{/if}
					</div>
					<div class="flex items-center gap-2.5 text-laya-secondary text-surface-400">
						<span>{groupedSources.length} source{groupedSources.length !== 1 ? 's' : ''}</span>
						{#if space.router_model || space.stager_model || space.chat_model || space.trace_model || space.omni_model}
							<span class="rounded {$glassTheme ? 'bg-white/[0.08]' : 'bg-surface-700'} px-1.5 py-0.5 text-laya-secondary">Custom models</span>
						{/if}
						{#if space.coding_agent}
							<span class="rounded {$glassTheme ? 'bg-white/[0.08]' : 'bg-surface-700'} px-1.5 py-0.5 text-laya-secondary">{agentLabel(space.coding_agent)}</span>
						{/if}
						{#if spaceRepoNames[space.space_id]?.length}
							<span class="rounded {$glassTheme ? 'bg-white/[0.08]' : 'bg-surface-700'} px-1.5 py-0.5 text-laya-secondary">{spaceRepoNames[space.space_id].length} repo{spaceRepoNames[space.space_id].length !== 1 ? 's' : ''}</span>
						{/if}
						<svg
							class="h-4 w-4 transition-transform {isExpanded ? 'rotate-180' : ''}"
							fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
						>
							<path d="M19 9l-7 7-7-7" />
						</svg>
					</div>
				</button>

				<!-- Expanded Detail -->
				{#if isExpanded}
					<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="border-t {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} p-4 space-y-5">
						{#if isEditing}
							<!-- Inline edit form -->
							{@render spaceForm(true)}
						{:else}
							<!-- Read-only detail view -->
							<!-- Sources -->
							<div>
								<div class="flex items-center justify-between mb-2">
									<h5 class="text-laya-base font-semibold text-surface-200">Sources</h5>
									<button
										onclick={() => startAssigning(space.space_id)}
										class="text-laya-base text-laya-orange hover:text-laya-orange/80 transition-colors"
									>
										+ Assign workflows
									</button>
								</div>
								{#if groupedSources.length === 0}
									<p class="text-laya-base text-surface-500 italic">No sources assigned. Click "Assign workflows" to add n8n ingestion workflows to this space.</p>
								{:else}
									<div class="space-y-1">
										{#each groupedSources as group (`${group.platform}::${group.displayName}`)}
											<div class="flex items-center gap-2 rounded-md {$glassTheme ? 'bg-white/[0.05]' : 'bg-surface-700/50'} px-3 py-2 text-laya-base">
												<span class="flex-1 truncate">{group.displayName}</span>
												<span class="text-laya-secondary text-surface-500">{group.platform}</span>
												<select
													value={group.sources[0].space_id}
													onchange={(e) => reassignSourceGroup(group, (e.target as HTMLSelectElement).value)}
													class="rounded border {$glassTheme ? 'bg-white/[0.06] border-white/[0.12]' : 'border-surface-600 bg-surface-700'} px-2 py-0.5 text-laya-secondary text-surface-300"
												>
													{#each spaces as s}
														<option value={s.space_id}>{s.name}</option>
													{/each}
												</select>
												<button
													onclick={() => removeSourceGroup(group)}
													class="text-laya-secondary text-red-400/60 hover:text-red-400 transition-colors"
													title="Unregister source"
												>
													✕
												</button>
											</div>
										{/each}
									</div>
								{/if}
							</div>

							<!-- Repositories -->
							{#if allRepos.length > 0}
								<div>
									<h5 class="text-laya-base font-semibold text-surface-200 mb-2">Repositories</h5>
									<p class="text-laya-base text-surface-500 mb-2">
										Assign repos to this space so engineer tasks pick the right codebase.
										{#if !spaceRepoNames[space.space_id]?.length}
											<span class="text-surface-400">No repos assigned — agent will search all repos.</span>
										{/if}
									</p>
									<div class="space-y-1">
										{#each allRepos as repo (repo.name)}
											{@const isAssigned = (spaceRepoNames[space.space_id] || []).includes(repo.name)}
											<button
												onclick={() => { toggleSpaceRepo(space.space_id, repo.name); saveSpaceRepos(space.space_id); }}
												class="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-laya-base transition-colors
													{isAssigned
														? 'repo-row-assigned text-laya-orange'
														: $glassTheme ? 'hover:bg-white/[0.06] text-surface-300' : 'hover:bg-surface-700 text-surface-300'}"
											>
												<span class="h-4 w-4 shrink-0 rounded border text-center text-laya-secondary leading-4
													{isAssigned ? 'border-laya-orange bg-laya-orange text-white' : 'border-surface-500'}">
													{#if isAssigned}&#10003;{/if}
												</span>
												<span class="flex-1 truncate">{repo.name}</span>
												{#if repo.remote_id}
													<span class="text-laya-secondary text-surface-500">{repo.remote_id}</span>
												{/if}
											</button>
										{/each}
									</div>
								</div>
							{/if}

							<!-- Model & Agent Configuration -->
							<div>
								<h5 class="text-laya-base font-semibold text-surface-200 mb-3">Model & Agent Configuration</h5>
								<div class="{$glassTheme ? 'rounded-lg border border-white/[0.06] divide-y divide-white/[0.05]' : 'rounded-lg border border-surface-700 divide-y divide-surface-700'}">
									<!-- Pipeline Models -->
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Router</span>
											<p class="text-laya-secondary text-surface-500">Classifies incoming events</p>
										</div>
										<span class="text-laya-base text-surface-400">{modelLabel(space.router_model)}</span>
									</div>
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Stager</span>
											<p class="text-laya-secondary text-surface-500">Stages actions from events</p>
										</div>
										<span class="text-laya-base text-surface-400">{modelLabel(space.stager_model)}</span>
									</div>
									<!-- Interactive Models -->
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Chat</span>
											<p class="text-laya-secondary text-surface-500">Conversational assistant</p>
										</div>
										<span class="text-laya-base text-surface-400">{modelLabel(space.chat_model)}</span>
									</div>
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Coherence</span>
											<p class="text-laya-secondary text-surface-500">Narratives & summaries</p>
										</div>
										<span class="text-laya-base text-surface-400">{modelLabel(space.trace_model)}</span>
									</div>
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Omni</span>
											<p class="text-laya-secondary text-surface-500">Cross-platform digest</p>
										</div>
										<span class="text-laya-base text-surface-400">{modelLabel(space.omni_model)}</span>
									</div>
									<!-- Agent -->
									<div class="px-4 py-2.5 flex items-center justify-between">
										<div>
											<span class="text-laya-base text-surface-200">Coding Agent</span>
											<p class="text-laya-secondary text-surface-500">CLI agent for engineer tasks</p>
										</div>
										<span class="text-laya-base text-surface-400">{agentLabel(space.coding_agent)}</span>
									</div>
								</div>
							</div>

							<!-- API Keys toggle -->
							<div>
								<button
									onclick={() => keySpaceId === space.space_id ? (keySpaceId = null) : openSpaceKeys(space.space_id)}
									class="text-laya-base font-semibold text-surface-200 hover:text-surface-100 transition-colors"
								>
									{keySpaceId === space.space_id ? '▾' : '▸'} API Keys
								</button>
								{#if keySpaceId === space.space_id}
									<div class="mt-2 space-y-2.5">
										<p class="text-laya-base text-surface-500">Override global API keys for this space. Uses global key when not set.</p>
										{#each providers as provider}
											<div class="flex items-center gap-2">
												<span class="w-20 text-laya-base text-surface-400">{provider.label}</span>
												{#if spaceKeyStatus[provider.id]}
													<span class="text-laya-secondary text-green-400">Configured</span>
													<button
														onclick={() => removeSpaceKey(provider.id)}
														class="ml-auto text-laya-secondary text-red-400/60 hover:text-red-400 transition-colors"
													>
														Remove
													</button>
												{:else}
													<input
														type="password"
														bind:value={spaceKeyInputs[provider.id]}
														placeholder="Enter API key..."
														class="flex-1 rounded border border-surface-600 bg-surface-700 px-2 py-1 text-laya-secondary text-surface-100 placeholder:text-surface-500"
													/>
													<button
														onclick={() => saveSpaceKey(provider.id)}
														disabled={!spaceKeyInputs[provider.id]?.trim() || savingSpaceKey === provider.id}
														class="rounded bg-surface-600 px-2 py-1 text-laya-secondary text-surface-200 hover:bg-surface-500 disabled:opacity-50 transition-colors"
													>
														{savingSpaceKey === provider.id ? '...' : 'Save'}
													</button>
												{/if}
											</div>
										{/each}
									</div>
								{/if}
							</div>

							<!-- Actions -->
							<div class="flex gap-3 pt-2 border-t border-surface-700/50">
								{#if !space.is_default}
									<button
										onclick={() => startEdit(space)}
										class="text-laya-base text-surface-400 hover:text-surface-200 transition-colors"
									>
										Edit
									</button>
									<button
										onclick={() => deleteSpace(space)}
										class="text-laya-base text-red-400/60 hover:text-red-400 transition-colors"
									>
										Delete
									</button>
								{:else}
									<button
										onclick={() => startEdit(space)}
										class="text-laya-base text-surface-400 hover:text-surface-200 transition-colors"
									>
										Edit models
									</button>
								{/if}
							</div>
						{/if}
					</div>
				{/if}
			</div>
		{/each}

		<!-- Workflow Assignment Modal -->
		{#if assigningSpaceId}
			{@const targetSpace = spaces.find((s) => s.space_id === assigningSpaceId)}
			<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
				<div class="mx-4 w-full max-w-lg rounded-xl border p-5 shadow-xl {$glassTheme ? 'glass-dropdown border-white/[0.12]' : 'border-surface-600 bg-surface-800'}">
					<h4 class="mb-1 text-base font-semibold text-surface-50">Assign Integrations to {targetSpace?.name}</h4>
					<p class="mb-4 text-laya-base text-surface-400">Select integrations to assign to this space.</p>

					<!-- Search -->
					<div class="relative mb-3">
						<svg class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
						</svg>
						<input
							type="text"
							bind:value={workflowSearch}
							placeholder="Search integrations..."
							class="w-full rounded-lg border border-surface-600 bg-surface-700 py-2 pl-9 pr-3 text-laya-base text-surface-100 placeholder:text-surface-500 focus:border-laya-orange/40 focus:outline-none"
						/>
					</div>

					{#if workflows.length === 0}
						<p class="py-4 text-center text-laya-base text-surface-400">No Laya ingestion workflows found in n8n.</p>
					{:else if filteredWorkflows.length === 0}
						<p class="py-4 text-center text-laya-base text-surface-400">No workflows matching "{workflowSearch}"</p>
					{:else}
						<div class="max-h-72 space-y-1 overflow-y-auto">
							{#each filteredWorkflows as wf (wf.workflow_id)}
								{@const isOwnedByTarget = wf.workflow_ids.some((id: string) => sources.find((s) => s.workflow_id === id && s.space_id === assigningSpaceId))}
								{@const currentSpace = sources.find((s) => wf.workflow_ids.includes(s.workflow_id))}
								<button
									onclick={() => toggleWorkflow(wf.workflow_ids)}
									disabled={!!isOwnedByTarget}
									class="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-laya-base transition-colors
										{selectedWorkflows.has(wf.workflow_ids[0])
											? 'bg-laya-orange/15 text-laya-orange'
											: isOwnedByTarget
												? ($glassTheme ? 'bg-white/[0.04] text-surface-500' : 'bg-surface-700/30 text-surface-500')
												: ($glassTheme ? 'hover:bg-white/[0.06] text-surface-300' : 'hover:bg-surface-700 text-surface-300')}"
								>
									<div class="min-w-0 flex-1">
										<p class="truncate">{wf.name}</p>
										<p class="mt-0.5 text-laya-secondary text-surface-500">
											{wf.platform}
											{#if currentSpace}
												<span class="text-surface-600">· in {currentSpace.space_name || 'Default'}</span>
											{/if}
										</p>
									</div>
									{#if selectedWorkflows.has(wf.workflow_ids[0])}
										<span class="text-laya-orange">✓</span>
									{:else if isOwnedByTarget}
										<span class="text-laya-secondary text-surface-500">Already here</span>
									{/if}
								</button>
							{/each}
						</div>
					{/if}

					<div class="mt-4 flex justify-end gap-2">
						<button
							onclick={cancelAssigning}
							class="rounded-md px-4 py-2 text-laya-base text-surface-400 hover:text-surface-200 transition-colors"
						>
							Cancel
						</button>
						<button
							onclick={assignSelected}
							disabled={selectedWorkflows.size === 0 || saving}
							class="rounded-md bg-laya-orange px-4 py-2 text-laya-base font-medium text-white hover:bg-laya-orange/90 disabled:opacity-50 transition-colors"
						>
							{saving ? 'Assigning...' : `Assign ${filteredWorkflows.filter(w => selectedWorkflows.has(w.workflow_id)).length} integration${filteredWorkflows.filter(w => selectedWorkflows.has(w.workflow_id)).length !== 1 ? 's' : ''}`}
						</button>
					</div>
				</div>
			</div>
		{/if}
	</div>
{/if}

<!-- Reusable edit form snippet -->
{#snippet spaceForm(isInline: boolean)}
	<div class="space-y-4">
		<!-- Name -->
		<div>
			<label for="space-name" class="mb-1 block text-laya-base text-surface-400">Name</label>
			<input
				id="space-name"
				type="text"
				bind:value={formName}
				placeholder="e.g. Work, Personal"
				maxlength="50"
				class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
			/>
		</div>

		<!-- Description -->
		<div>
			<label for="space-desc" class="mb-1 block text-laya-base text-surface-400">Description <span class="text-surface-500">(optional)</span></label>
			<input
				id="space-desc"
				type="text"
				bind:value={formDescription}
				placeholder="What this space is for..."
				class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
			/>
		</div>

		<!-- Color -->
		<div>
			<!-- svelte-ignore a11y_label_has_associated_control -->
			<label class="mb-2 block text-laya-base text-surface-400">Color</label>
			<div class="flex gap-2">
				{#each presetColors as color}
					<button
						onclick={() => (formColor = color)}
						class="h-7 w-7 rounded-full border-2 transition-transform hover:scale-110
							{formColor === color ? 'border-white scale-110' : 'border-transparent'}"
						style="background-color: {color}"
						aria-label="Select color {color}"
					></button>
				{/each}
			</div>
		</div>

		<!-- Model & Agent Configuration -->
		<div>
			<!-- svelte-ignore a11y_label_has_associated_control -->
			<label class="mb-3 block text-laya-base text-surface-400">Model & Agent Configuration</label>
			<div class="{$glassTheme ? 'rounded-lg border border-white/[0.06] divide-y divide-white/[0.05]' : 'rounded-lg border border-surface-700 divide-y divide-surface-700'}">
				<!-- Inference backend: provider dropdowns vs installed agent (typed model strings) -->
				<div class="px-4 py-3">
					<div class="flex items-center gap-3">
						<span class="text-laya-base text-surface-200">Backend</span>
						<div class="ml-auto inline-flex rounded-md border {$glassTheme ? 'border-white/15' : 'border-surface-600'} p-0.5">
							<button type="button" onclick={() => setFormAgentMode(false)}
								class="rounded px-2.5 py-1 text-laya-base transition-colors {!formAgentMode ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-300'}">Provider</button>
							<button type="button" onclick={() => setFormAgentMode(true)}
								class="inline-flex items-center gap-1 rounded px-2.5 py-1 text-laya-base transition-colors {formAgentMode ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-300'}">Agent
								<span class="rounded bg-laya-gold/25 px-1 text-laya-micro font-semibold uppercase tracking-wide text-laya-amber">Beta</span>
							</button>
						</div>
					</div>
					{#if formAgentMode}
						<div class="mt-2 flex flex-wrap gap-1.5">
							{#each agentBackends as b}
								<button type="button" onclick={() => b.available && selectFormAgent(b.agent_id)} disabled={!b.available} title={b.hint}
									class="flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-laya-secondary transition-colors {formSelectedAgent === b.agent_id ? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange' : 'border-surface-600 text-surface-300 hover:border-surface-500'} {!b.available ? 'cursor-not-allowed opacity-50' : ''}">
									{AGENT_LABELS[b.agent_id] || b.agent_id}
									<span class="rounded px-1 text-laya-micro {b.tier === 'native' ? 'bg-laya-gold/25 text-laya-amber' : 'bg-surface-700 text-surface-400'}">{b.tier === 'native' ? 'native' : 'best-effort'}</span>
								</button>
							{/each}
						</div>
						<p class="mt-1.5 text-laya-micro text-surface-500">Chat &amp; Coherence keep using a model provider (agents can’t stream those).</p>
					{/if}
				</div>
				<!-- Pipeline Models -->
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Router</span>
						<p class="text-laya-secondary text-surface-500">Classifies incoming events</p>
					</div>
					<div class="w-48 shrink-0">
						{#if formAgentMode}
							<input type="text" value={formAgentModelString('router')} onchange={handleFormAgentInput('router')}
								placeholder={AGENT_MODEL_PH[formSelectedAgent] || 'model (blank = default)'} spellcheck="false" autocapitalize="off"
								class="w-full rounded-md border px-3 py-2 font-mono text-laya-base text-surface-100 placeholder:text-surface-500 focus:outline-none {$glassTheme ? 'glass-input' : 'border-surface-600 bg-surface-700 focus:border-surface-500'}" />
						{:else}
							<ModelSelect
								bind:value={formRouterModel}
								providers={availableModels}
								onchange={(v) => (formRouterModel = v)}
								allowEmpty={true}
								emptyLabel="Use default"
							/>
						{/if}
					</div>
				</div>
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Stager</span>
						<p class="text-laya-secondary text-surface-500">Stages actions from events</p>
					</div>
					<div class="w-48 shrink-0">
						{#if formAgentMode}
							<input type="text" value={formAgentModelString('stager')} onchange={handleFormAgentInput('stager')}
								placeholder={AGENT_MODEL_PH[formSelectedAgent] || 'model (blank = default)'} spellcheck="false" autocapitalize="off"
								class="w-full rounded-md border px-3 py-2 font-mono text-laya-base text-surface-100 placeholder:text-surface-500 focus:outline-none {$glassTheme ? 'glass-input' : 'border-surface-600 bg-surface-700 focus:border-surface-500'}" />
						{:else}
							<ModelSelect
								bind:value={formStagerModel}
								providers={availableModels}
								onchange={(v) => (formStagerModel = v)}
								allowEmpty={true}
								emptyLabel="Use default"
							/>
						{/if}
					</div>
				</div>
				<!-- Interactive Models -->
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Chat</span>
						<p class="text-laya-secondary text-surface-500">Conversational assistant</p>
					</div>
					<div class="w-48 shrink-0">
						<ModelSelect
							bind:value={formChatModel}
							providers={availableModels}
							onchange={(v) => (formChatModel = v)}
							allowEmpty={true}
							emptyLabel="Use default"
						/>
					</div>
				</div>
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Coherence</span>
						<p class="text-laya-secondary text-surface-500">Narratives & summaries</p>
					</div>
					<div class="w-48 shrink-0">
						<ModelSelect
							bind:value={formTraceModel}
							providers={availableModels}
							onchange={(v) => (formTraceModel = v)}
							allowEmpty={true}
							emptyLabel="Use default"
						/>
					</div>
				</div>
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Omni</span>
						<p class="text-laya-secondary text-surface-500">Cross-platform digest</p>
					</div>
					<div class="w-48 shrink-0">
						{#if formAgentMode}
							<input type="text" value={formAgentModelString('omni')} onchange={handleFormAgentInput('omni')}
								placeholder={AGENT_MODEL_PH[formSelectedAgent] || 'model (blank = default)'} spellcheck="false" autocapitalize="off"
								class="w-full rounded-md border px-3 py-2 font-mono text-laya-base text-surface-100 placeholder:text-surface-500 focus:outline-none {$glassTheme ? 'glass-input' : 'border-surface-600 bg-surface-700 focus:border-surface-500'}" />
						{:else}
							<ModelSelect
								bind:value={formOmniModel}
								providers={availableModels}
								onchange={(v) => (formOmniModel = v)}
								allowEmpty={true}
								emptyLabel="Use default"
							/>
						{/if}
					</div>
				</div>
				<!-- Coding Agent -->
				<div class="px-4 py-3 flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<span class="text-laya-base text-surface-200">Coding Agent</span>
						<p class="text-laya-secondary text-surface-500">CLI agent for engineer tasks</p>
					</div>
					<div class="w-48 shrink-0">
						<select
							value={formCodingAgent}
							onchange={(e) => (formCodingAgent = (e.target as HTMLSelectElement).value)}
							class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100"
						>
							{#each agentOptions as opt}
								<option value={opt.value}>{opt.label}</option>
							{/each}
						</select>
					</div>
				</div>
			</div>
		</div>

		<!-- Actions -->
		<div class="flex gap-2 pt-2">
			<button
				onclick={saveSpace}
				disabled={!formName.trim() || saving}
				class="rounded-md bg-laya-orange px-4 py-2 text-laya-base font-medium text-white transition-colors hover:bg-laya-orange/90 disabled:opacity-50"
			>
				{saving ? 'Saving...' : isInline ? 'Update Space' : 'Create Space'}
			</button>
			<button
				onclick={cancelForm}
				class="rounded-md px-4 py-2 text-laya-base text-surface-400 transition-colors hover:text-surface-200"
			>
				Cancel
			</button>
		</div>
	</div>
{/snippet}
