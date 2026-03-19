<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { Space, Source, AvailableWorkflow, Repo, ProviderModels } from '$lib/api/types';
	import ModelSelect from './ModelSelect.svelte';

	const providers = [
		{ id: 'anthropic', label: 'Anthropic' },
		{ id: 'openai', label: 'OpenAI' },
		{ id: 'google', label: 'Google' },
		{ id: 'openrouter', label: 'OpenRouter' }
	];

	let availableModels = $state<ProviderModels[]>([]);

	const agentOptions = [
		{ value: '', label: 'Use default' },
		{ value: 'claude_code', label: 'Claude Code' },
		{ value: 'gemini_cli', label: 'Gemini CLI' },
		{ value: 'codex_cli', label: 'Codex CLI' }
	];

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

	// Form state
	let editingSpace = $state<Space | null>(null);
	let showCreateForm = $state(false);
	let formName = $state('');
	let formDescription = $state('');
	let formIcon = $state('📁');
	let formColor = $state('#3B82F6');
	let formRouterModel = $state('');
	let formStagerModel = $state('');
	let formChatModel = $state('');
	let formCodingAgent = $state('');
	let saving = $state(false);

	// Source assignment state
	let assigningSpaceId = $state<string | null>(null);
	let selectedWorkflows = $state<Set<string>>(new Set());

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
		editingSpace = null;
		formName = '';
		formDescription = '';
		formIcon = '📁';
		formColor = '#3B82F6';
		formRouterModel = '';
		formStagerModel = '';
		formChatModel = '';
		formCodingAgent = '';
	}

	function startEdit(space: Space) {
		showCreateForm = false;
		editingSpace = space;
		formName = space.name;
		formDescription = space.description || '';
		formIcon = space.icon;
		formColor = space.color;
		formRouterModel = space.router_model || '';
		formStagerModel = space.stager_model || '';
		formChatModel = space.chat_model || '';
		formCodingAgent = space.coding_agent || '';
	}

	function cancelForm() {
		showCreateForm = false;
		editingSpace = null;
	}

	async function saveSpace() {
		if (!formName.trim()) return;
		saving = true;
		try {
			if (editingSpace) {
				await engineApi.updateSpace(editingSpace.space_id, {
					name: formName.trim(),
					description: formDescription.trim() || undefined,
					icon: formIcon,
					color: formColor,
					router_model: formRouterModel || undefined,
					stager_model: formStagerModel || undefined,
					chat_model: formChatModel || undefined,
					coding_agent: formCodingAgent || undefined
				});
			} else {
				await engineApi.createSpace({
					name: formName.trim(),
					description: formDescription.trim() || undefined,
					icon: formIcon,
					color: formColor,
					router_model: formRouterModel || undefined,
					stager_model: formStagerModel || undefined,
					chat_model: formChatModel || undefined,
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

	// Source assignment
	async function startAssigning(spaceId: string) {
		assigningSpaceId = spaceId;
		selectedWorkflows = new Set();
		await loadWorkflows();
	}

	function cancelAssigning() {
		assigningSpaceId = null;
		selectedWorkflows = new Set();
	}

	function toggleWorkflow(wfId: string) {
		const next = new Set(selectedWorkflows);
		if (next.has(wfId)) next.delete(wfId);
		else next.add(wfId);
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
	function sourcesForSpace(spaceId: string): Source[] {
		return sources.filter((s) => s.space_id === spaceId);
	}

	function toggleExpand(spaceId: string) {
		if (expandedSpaceId === spaceId) {
			expandedSpaceId = null;
		} else {
			expandedSpaceId = spaceId;
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
				<h3 class="text-lg font-medium">Spaces</h3>
				<p class="text-sm text-surface-400">
					Group event sources and assign specific models or API keys per space.
				</p>
			</div>
			{#if !showCreateForm && !editingSpace}
				<button
					onclick={startCreate}
					class="rounded-md bg-laya-orange/15 px-4 py-2 text-sm font-medium text-laya-orange transition-colors hover:bg-laya-orange/25"
				>
					+ New Space
				</button>
			{/if}
		</div>

		<!-- Create / Edit Form -->
		{#if showCreateForm || editingSpace}
			<div class="rounded-lg border border-laya-orange/30 bg-surface-800 p-5">
				<h4 class="mb-4 font-medium">{editingSpace ? `Edit "${editingSpace.name}"` : 'New Space'}</h4>

				<div class="space-y-4">
					<!-- Name + Icon -->
					<div class="flex gap-3">
						<div class="flex-1">
							<label for="space-name" class="mb-1 block text-sm text-surface-400">Name</label>
							<input
								id="space-name"
								type="text"
								bind:value={formName}
								placeholder="e.g. Work, Personal"
								maxlength="50"
								class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
							/>
						</div>
						<div class="w-20">
							<label for="space-icon" class="mb-1 block text-sm text-surface-400">Icon</label>
							<input
								id="space-icon"
								type="text"
								bind:value={formIcon}
								maxlength="2"
								class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-center text-lg"
							/>
						</div>
					</div>

					<!-- Description -->
					<div>
						<label for="space-desc" class="mb-1 block text-sm text-surface-400">Description <span class="text-surface-500">(optional)</span></label>
						<input
							id="space-desc"
							type="text"
							bind:value={formDescription}
							placeholder="What this space is for..."
							class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
						/>
					</div>

					<!-- Color -->
					<div>
						<!-- svelte-ignore a11y_label_has_associated_control -->
						<label class="mb-2 block text-sm text-surface-400">Color</label>
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

					<!-- Model Overrides -->
					<div>
						<!-- svelte-ignore a11y_label_has_associated_control -->
						<label class="mb-2 block text-sm text-surface-400">Model Overrides</label>
						<div class="grid grid-cols-3 gap-3">
							<div>
								<span class="mb-1 block text-xs text-surface-500">Router</span>
								<ModelSelect
									bind:value={formRouterModel}
									providers={availableModels}
									onchange={(v) => (formRouterModel = v)}
									allowEmpty={true}
									emptyLabel="Use default"
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs text-surface-500">Stager</span>
								<ModelSelect
									bind:value={formStagerModel}
									providers={availableModels}
									onchange={(v) => (formStagerModel = v)}
									allowEmpty={true}
									emptyLabel="Use default"
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs text-surface-500">Chat</span>
								<ModelSelect
									bind:value={formChatModel}
									providers={availableModels}
									onchange={(v) => (formChatModel = v)}
									allowEmpty={true}
									emptyLabel="Use default"
								/>
							</div>
						</div>
					</div>

					<!-- Coding Agent Override -->
					<div>
						<label for="space-agent" class="mb-2 block text-sm text-surface-400">Coding Agent</label>
						<select
							id="space-agent"
							value={formCodingAgent}
							onchange={(e) => (formCodingAgent = (e.target as HTMLSelectElement).value)}
							class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100"
						>
							{#each agentOptions as opt}
								<option value={opt.value}>{opt.label}</option>
							{/each}
						</select>
						<p class="mt-1 text-xs text-surface-500">CLI agent used for ENGINEER tasks in this space</p>
					</div>

					<!-- Actions -->
					<div class="flex gap-2 pt-2">
						<button
							onclick={saveSpace}
							disabled={!formName.trim() || saving}
							class="rounded-md bg-laya-orange px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-laya-orange/90 disabled:opacity-50"
						>
							{saving ? 'Saving...' : editingSpace ? 'Update Space' : 'Create Space'}
						</button>
						<button
							onclick={cancelForm}
							class="rounded-md px-4 py-2 text-sm text-surface-400 transition-colors hover:text-surface-200"
						>
							Cancel
						</button>
					</div>
				</div>
			</div>
		{/if}

		<!-- Spaces List -->
		{#each spaces as space (space.space_id)}
			{@const spaceSources = sourcesForSpace(space.space_id)}
			<div class="rounded-lg border bg-surface-800 transition-colors
				{space.paused ? 'border-laya-amber/30 border-dashed' : expandedSpaceId === space.space_id ? 'border-laya-orange/30' : 'border-surface-700'}">
				<!-- Space Header -->
				<button
					onclick={() => toggleExpand(space.space_id)}
					class="flex w-full items-center gap-3 p-4 text-left"
				>
					<span class="text-xl">{space.icon}</span>
					<div
						class="h-3 w-3 rounded-full shrink-0"
						style="background-color: {space.color}"
					></div>
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2">
							<span class="font-medium">{space.name}</span>
							{#if space.is_default}
								<span class="rounded bg-surface-600 px-1.5 py-0.5 text-[10px] text-surface-400">DEFAULT</span>
							{/if}
							{#if space.paused}
								<span class="rounded bg-laya-amber/20 px-1.5 py-0.5 text-[10px] font-medium text-laya-amber">PAUSED</span>
							{/if}
						</div>
						{#if space.description}
							<p class="truncate text-xs text-surface-500">{space.description}</p>
						{/if}
					</div>
					<div class="flex items-center gap-3 text-xs text-surface-400">
						<span>{spaceSources.length} source{spaceSources.length !== 1 ? 's' : ''}</span>
						{#if space.router_model || space.stager_model || space.chat_model}
							<span class="rounded bg-surface-700 px-1.5 py-0.5">Custom models</span>
						{/if}
						{#if space.coding_agent}
							<span class="rounded bg-surface-700 px-1.5 py-0.5">{agentLabel(space.coding_agent)}</span>
						{/if}
						{#if spaceRepoNames[space.space_id]?.length}
							<span class="rounded bg-surface-700 px-1.5 py-0.5">{spaceRepoNames[space.space_id].length} repo{spaceRepoNames[space.space_id].length !== 1 ? 's' : ''}</span>
						{/if}
						<svg
							class="h-4 w-4 transition-transform {expandedSpaceId === space.space_id ? 'rotate-180' : ''}"
							fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
						>
							<path d="M19 9l-7 7-7-7" />
						</svg>
					</div>
				</button>
				{#if spaceSources.length > 0}
					<div class="flex justify-end px-4 -mt-2 pb-2">
						<button
							onclick={() => togglePause(space)}
							disabled={togglingPause === space.space_id}
							class="rounded-md px-2 py-1 text-xs font-medium transition-colors
								{space.paused
									? 'bg-laya-orange/15 text-laya-orange hover:bg-laya-orange/25'
									: 'bg-surface-700 text-surface-400 hover:bg-surface-600 hover:text-surface-200'}
								disabled:opacity-50"
							title={space.paused ? 'Resume all ingestion workflows' : 'Pause all ingestion workflows'}
						>
							{#if togglingPause === space.space_id}
								...
							{:else if space.paused}
								▶ Resume
							{:else}
								⏸ Pause
							{/if}
						</button>
					</div>
				{/if}

				<!-- Expanded Detail -->
				{#if expandedSpaceId === space.space_id}
					<div class="border-t border-surface-700 p-4 space-y-4">
						<!-- Sources -->
						<div>
							<div class="flex items-center justify-between mb-2">
								<h5 class="text-sm font-medium text-surface-300">Sources</h5>
								<button
									onclick={() => startAssigning(space.space_id)}
									class="text-xs text-laya-orange hover:text-laya-orange/80 transition-colors"
								>
									+ Assign workflows
								</button>
							</div>
							{#if spaceSources.length === 0}
								<p class="text-xs text-surface-500 italic">No sources assigned. Click "Assign workflows" to add n8n ingestion workflows to this space.</p>
							{:else}
								<div class="space-y-1">
									{#each spaceSources as source (source.source_id)}
										<div class="flex items-center gap-2 rounded-md bg-surface-700/50 px-3 py-2 text-sm">
											<span class="flex-1 truncate">{source.name}</span>
											<span class="text-xs text-surface-500">{source.platform}</span>
											<!-- Reassign dropdown -->
											<select
												value={source.space_id}
												onchange={(e) => reassignSource(source.source_id, (e.target as HTMLSelectElement).value)}
												class="rounded border border-surface-600 bg-surface-700 px-2 py-0.5 text-xs text-surface-300"
											>
												{#each spaces as s}
													<option value={s.space_id}>{s.icon} {s.name}</option>
												{/each}
											</select>
											<button
												onclick={() => removeSource(source.source_id)}
												class="text-xs text-red-400/60 hover:text-red-400 transition-colors"
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
								<h5 class="text-sm font-medium text-surface-300 mb-2">Repositories</h5>
								<p class="text-xs text-surface-500 mb-2">
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
											class="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors
												{isAssigned
													? 'bg-laya-orange/15 text-laya-orange'
													: 'hover:bg-surface-700 text-surface-300'}"
										>
											<span class="h-4 w-4 shrink-0 rounded border text-center text-xs leading-4
												{isAssigned ? 'border-laya-orange bg-laya-orange text-white' : 'border-surface-500'}">
												{#if isAssigned}&#10003;{/if}
											</span>
											<span class="flex-1 truncate">{repo.name}</span>
											{#if repo.remote_id}
												<span class="text-xs text-surface-500">{repo.remote_id}</span>
											{/if}
										</button>
									{/each}
								</div>
							</div>
						{/if}

						<!-- Model overrides summary -->
						<div>
							<h5 class="text-sm font-medium text-surface-300 mb-2">Model & Agent Configuration</h5>
							<div class="grid grid-cols-4 gap-2 text-xs">
								<div class="rounded bg-surface-700/50 p-2">
									<span class="text-surface-500">Router</span>
									<p class="text-surface-300">{modelLabel(space.router_model)}</p>
								</div>
								<div class="rounded bg-surface-700/50 p-2">
									<span class="text-surface-500">Stager</span>
									<p class="text-surface-300">{modelLabel(space.stager_model)}</p>
								</div>
								<div class="rounded bg-surface-700/50 p-2">
									<span class="text-surface-500">Chat</span>
									<p class="text-surface-300">{modelLabel(space.chat_model)}</p>
								</div>
								<div class="rounded bg-surface-700/50 p-2">
									<span class="text-surface-500">Agent</span>
									<p class="text-surface-300">{agentLabel(space.coding_agent)}</p>
								</div>
							</div>
						</div>

						<!-- API Keys toggle -->
						<div>
							<button
								onclick={() => keySpaceId === space.space_id ? (keySpaceId = null) : openSpaceKeys(space.space_id)}
								class="text-sm text-surface-400 hover:text-surface-200 transition-colors"
							>
								{keySpaceId === space.space_id ? '▾' : '▸'} API Keys
							</button>
							{#if keySpaceId === space.space_id}
								<div class="mt-2 space-y-2">
									<p class="text-xs text-surface-500">Override global API keys for this space. Uses global key when not set.</p>
									{#each providers as provider}
										<div class="flex items-center gap-2">
											<span class="w-20 text-xs text-surface-400">{provider.label}</span>
											{#if spaceKeyStatus[provider.id]}
												<span class="text-xs text-green-400">Configured</span>
												<button
													onclick={() => removeSpaceKey(provider.id)}
													class="ml-auto text-xs text-red-400/60 hover:text-red-400 transition-colors"
												>
													Remove
												</button>
											{:else}
												<input
													type="password"
													bind:value={spaceKeyInputs[provider.id]}
													placeholder="Enter API key..."
													class="flex-1 rounded border border-surface-600 bg-surface-700 px-2 py-1 text-xs text-surface-100 placeholder:text-surface-500"
												/>
												<button
													onclick={() => saveSpaceKey(provider.id)}
													disabled={!spaceKeyInputs[provider.id]?.trim() || savingSpaceKey === provider.id}
													class="rounded bg-surface-600 px-2 py-1 text-xs text-surface-200 hover:bg-surface-500 disabled:opacity-50 transition-colors"
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
						<div class="flex gap-2 pt-1 border-t border-surface-700/50">
							{#if !space.is_default}
								<button
									onclick={() => startEdit(space)}
									class="text-xs text-surface-400 hover:text-surface-200 transition-colors"
								>
									Edit
								</button>
								<button
									onclick={() => deleteSpace(space)}
									class="text-xs text-red-400/60 hover:text-red-400 transition-colors"
								>
									Delete
								</button>
							{:else}
								<button
									onclick={() => startEdit(space)}
									class="text-xs text-surface-400 hover:text-surface-200 transition-colors"
								>
									Edit models
								</button>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		{/each}

		<!-- Workflow Assignment Modal -->
		{#if assigningSpaceId}
			{@const targetSpace = spaces.find((s) => s.space_id === assigningSpaceId)}
			<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
				<div class="mx-4 w-full max-w-lg rounded-lg border border-surface-600 bg-surface-800 p-5 shadow-xl">
					<h4 class="mb-1 font-medium">Assign Workflows to {targetSpace?.icon} {targetSpace?.name}</h4>
					<p class="mb-4 text-xs text-surface-500">Select n8n ingestion workflows to assign to this space.</p>

					{#if workflows.length === 0}
						<p class="py-4 text-center text-sm text-surface-400">No Laya ingestion workflows found in n8n.</p>
					{:else}
						<div class="max-h-64 space-y-1 overflow-y-auto">
							{#each workflows as wf (wf.workflow_id)}
								{@const isOwnedByTarget = sources.find((s) => s.workflow_id === wf.workflow_id && s.space_id === assigningSpaceId)}
								<button
									onclick={() => toggleWorkflow(wf.workflow_id)}
									disabled={!!isOwnedByTarget}
									class="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors
										{selectedWorkflows.has(wf.workflow_id)
											? 'bg-laya-orange/15 text-laya-orange'
											: isOwnedByTarget
												? 'bg-surface-700/30 text-surface-500'
												: 'hover:bg-surface-700 text-surface-300'}"
								>
									<div class="min-w-0 flex-1">
										<p class="truncate">{wf.name}</p>
										<p class="text-xs text-surface-500">
											{wf.platform}
											{#if wf.registered}
												{@const src = sources.find((s) => s.workflow_id === wf.workflow_id)}
												{#if src}
													<span class="text-surface-600">· in {src.space_name || 'Default'}</span>
												{/if}
											{/if}
										</p>
									</div>
									{#if selectedWorkflows.has(wf.workflow_id)}
										<span class="text-laya-orange">✓</span>
									{:else if isOwnedByTarget}
										<span class="text-xs text-surface-500">Already here</span>
									{/if}
								</button>
							{/each}
						</div>
					{/if}

					<div class="mt-4 flex justify-end gap-2">
						<button
							onclick={cancelAssigning}
							class="rounded-md px-4 py-2 text-sm text-surface-400 hover:text-surface-200 transition-colors"
						>
							Cancel
						</button>
						<button
							onclick={assignSelected}
							disabled={selectedWorkflows.size === 0 || saving}
							class="rounded-md bg-laya-orange px-4 py-2 text-sm font-medium text-white hover:bg-laya-orange/90 disabled:opacity-50 transition-colors"
						>
							{saving ? 'Assigning...' : `Assign ${selectedWorkflows.size} workflow${selectedWorkflows.size !== 1 ? 's' : ''}`}
						</button>
					</div>
				</div>
			</div>
		{/if}
	</div>
{/if}
