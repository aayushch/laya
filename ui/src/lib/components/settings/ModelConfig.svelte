<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { slide } from 'svelte/transition';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import type { ProviderModels, CustomProvider, CustomProviderTestResult, DiscoveredModel, PipelineSettings, BudgetConfig, MonthlyCostEntry } from '$lib/api/types';
	import { budgetPaused, loadBudgetStatus } from '$lib/stores/budget';
	import { portal } from '$lib/actions/portal';
	import ModelSelect from './ModelSelect.svelte';

	let guideTooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showGuide(e: MouseEvent, text: string) {
		const el = e.currentTarget as HTMLElement;
		const r = el.getBoundingClientRect();
		guideTooltip = { text, top: r.bottom + 8, left: r.left + r.width / 2 };
	}
	function hideGuide() { guideTooltip = null; }

	const roles = [
		{ id: 'router', label: 'Router', hint: 'Classifies incoming events',
			guide: 'Classifies each incoming event by persona (Engineer/Comms/Ops) and priority. Output is structured JSON, not prose. A small, fast model works well here.' },
		{ id: 'stager', label: 'Stager', hint: 'Synthesises action cards',
			guide: 'Reads the classified event and writes the action card: headline, summary, suggested actions, and draft replies. Quality of card content scales directly with model capability — use a stronger model if you want richer summaries.' },
		{ id: 'chat', label: 'Chat', hint: 'Conversational responses',
			guide: 'Powers the chat panel where you ask questions about your cards, events, and workspace. A capable model gives better conversational and reasoning quality.' },
		{ id: 'trace', label: 'Coherence', hint: 'Generates trace narratives',
			guide: 'Generates narrative summaries when you search for related activity across platforms. The heavy lifting is semantic search — the model just synthesises results into prose. A smaller model is usually sufficient.' },
		{ id: 'omni', label: 'Omni', hint: 'Resynthesises rolling summaries',
			guide: 'Periodically compresses your rolling cross-platform summary into a concise digest. Needs to merge and deduplicate information across many events. A mid-tier model balances cost and coherence well.' }
	];

	const cloudProviders = [
		{ id: 'anthropic', label: 'Anthropic', envVar: 'ANTHROPIC_API_KEY' },
		{ id: 'openai', label: 'OpenAI', envVar: 'OPENAI_API_KEY' },
		{ id: 'google', label: 'Google', envVar: 'GOOGLE_API_KEY' },
		{ id: 'openrouter', label: 'OpenRouter', envVar: 'OPENROUTER_API_KEY' }
	];

	const providerTypes = [
		{ id: 'lmstudio', label: 'LM Studio', defaultUrl: 'http://localhost:1234' },
		{ id: 'ollama', label: 'Ollama', defaultUrl: 'http://localhost:11434' },
		{ id: 'openai_compatible', label: 'OpenAI Compatible', defaultUrl: 'http://localhost:8080' }
	];

	let models = $state({
		router: 'claude-haiku-4-5',
		stager: 'claude-sonnet-4-6',
		chat: 'claude-sonnet-4-6',
		trace: 'claude-sonnet-4-6',
		omni: 'claude-sonnet-4-6',
		local: 'ollama/llama3'
	});

	let apiKeys = $state<Record<string, boolean>>({
		anthropic: false,
		openai: false,
		google: false,
		openrouter: false
	});

	let keyInputs = $state<Record<string, string>>({
		anthropic: '',
		openai: '',
		google: '',
		openrouter: ''
	});

	let saving = $state(false);
	let savingKey = $state<string | null>(null);
	let loaded = $state(false);
	let availableModels = $state<ProviderModels[]>([]);
	let modelsLoading = $state(false);

	// Local providers state
	let customProviders = $state<CustomProvider[]>([]);
	let showAddProvider = $state(false);
	let newProvider = $state({ name: '', base_url: '', provider_type: 'lmstudio', api_key: '' });
	let addingProvider = $state(false);
	let addError = $state('');

	// Per-provider state
	let testingProvider = $state<string | null>(null);
	let testResults = $state<Record<string, CustomProviderTestResult>>({});
	let providerModels = $state<Record<string, DiscoveredModel[]>>({});
	let expandedProvider = $state<string | null>(null);
	let deletingProvider = $state<string | null>(null);

	// Edit state
	let editingProvider = $state<string | null>(null);
	let editForm = $state({ name: '', base_url: '', api_key: '' });
	let editSaving = $state(false);

	// Advanced pipeline settings
	let showAdvanced = $state(false);
	let pipeline = $state<PipelineSettings>({
		model_timeout: 120,
		llm_retries: 3,
		max_retry_attempts: 5,
		max_concurrent_events: 5,
		queue_poll_interval: 2
	});
	let savingPipeline = $state(false);
	let pipelineSaveTimer: ReturnType<typeof setTimeout> | null = null;

	// Budget / Cost Control state
	let budgetEnabled = $state(false);
	let budgetLimit = $state<number | null>(null);
	let budgetLimitInput = $state<number | null>(null);
	let currentMonthCost = $state(0);
	let currentMonth = $state('');
	let budgetByModel = $state<Record<string, number>>({});
	let budgetIsPaused = $state(false);
	let pausedWorkflowCount = $state(0);
	let savingBudget = $state(false);
	let resumingBudget = $state(false);
	let showHistory = $state(false);
	let budgetHistory = $state<MonthlyCostEntry[]>([]);
	let historyLoading = $state(false);

	const budgetPercent = $derived(
		budgetLimit && budgetLimit > 0 ? Math.min((currentMonthCost / budgetLimit) * 100, 100) : 0
	);
	const budgetBarColor = $derived(
		budgetPercent >= 100 ? 'bg-red-500' : budgetPercent >= 75 ? 'bg-amber-500' : 'bg-green-500'
	);

	async function loadBudget() {
		try {
			const data = await engineApi.getBudget();
			budgetEnabled = data.enabled;
			budgetLimit = data.monthly_limit_usd;
			budgetLimitInput = data.monthly_limit_usd;
			currentMonthCost = data.current_month_cost;
			currentMonth = data.current_month;
			budgetByModel = data.by_model;
			budgetIsPaused = data.is_paused;
			pausedWorkflowCount = data.paused_workflow_count;
			budgetPaused.set(data.is_paused);
		} catch (e) {
			console.error('Failed to load budget:', e);
		}
	}

	async function saveBudget() {
		savingBudget = true;
		try {
			const limit = budgetLimitInput != null && budgetLimitInput > 0 ? budgetLimitInput : null;
			await engineApi.updateBudget({ monthly_limit_usd: limit, enabled: budgetEnabled });
			budgetLimit = limit;
			// Re-fetch to confirm server state
			await loadBudget();
			// Update the global budget store so the footer cost widget refreshes
			loadBudgetStatus();
		} catch (e) {
			console.error('Failed to save budget:', e);
		} finally {
			savingBudget = false;
		}
	}

	let budgetSaveTimer: ReturnType<typeof setTimeout> | null = null;
	function debounceSaveBudget() {
		if (budgetSaveTimer) clearTimeout(budgetSaveTimer);
		budgetSaveTimer = setTimeout(saveBudget, 600);
	}

	async function handleResume() {
		resumingBudget = true;
		try {
			await engineApi.resumeBudget();
			await loadBudget();
		} catch (e) {
			console.error('Failed to resume:', e);
		} finally {
			resumingBudget = false;
		}
	}

	async function loadHistory() {
		if (budgetHistory.length > 0) return; // already loaded
		historyLoading = true;
		try {
			const data = await engineApi.getBudgetHistory();
			budgetHistory = data.months;
		} catch (e) {
			console.error('Failed to load budget history:', e);
		} finally {
			historyLoading = false;
		}
	}

	function formatMonth(ym: string): string {
		const [y, m] = ym.split('-');
		const date = new Date(parseInt(y), parseInt(m) - 1);
		return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
	}

	onMount(async () => {
		try {
			const [settings, providersResp] = await Promise.all([
				engineApi.getSettings(),
				engineApi.getCustomProviders()
			]);
			models = { ...models, ...settings.models };
			apiKeys = { ...apiKeys, ...settings.api_keys };
			if (settings.pipeline) pipeline = { ...pipeline, ...settings.pipeline };
			customProviders = providersResp.providers;
			loaded = true;
			await Promise.all([fetchModels(), loadBudget()]);
		} catch (e) {
			console.error('Failed to load settings:', e);
		}
	});

	async function fetchModels(refresh = false) {
		modelsLoading = true;
		try {
			const resp = await engineApi.getAvailableModels(refresh);
			availableModels = resp.providers;
		} catch (e) {
			console.error('Failed to fetch available models:', e);
		} finally {
			modelsLoading = false;
		}
	}

	async function saveModels() {
		saving = true;
		try {
			await engineApi.updateSettings({ models } as any);
		} catch (e) {
			console.error('Failed to save models:', e);
		} finally {
			saving = false;
		}
	}

	function handleModelChange(role: string) {
		return (value: string) => {
			models[role as keyof typeof models] = value;
			saveModels();
		};
	}

	async function saveApiKey(provider: string) {
		const key = keyInputs[provider];
		if (!key.trim()) return;
		savingKey = provider;
		try {
			await engineApi.setApiKey(provider, key.trim());
			apiKeys[provider] = true;
			keyInputs[provider] = '';
			await fetchModels();
		} catch (e) {
			console.error('Failed to save API key:', e);
		} finally {
			savingKey = null;
		}
	}

	async function removeApiKey(provider: string) {
		try {
			await engineApi.deleteApiKey(provider);
			apiKeys[provider] = false;
			await fetchModels();
		} catch (e) {
			console.error('Failed to remove API key:', e);
		}
	}

	// --- Local provider management ---

	function handleProviderTypeChange(type: string) {
		newProvider.provider_type = type;
		const preset = providerTypes.find(p => p.id === type);
		if (preset && !newProvider.base_url) {
			newProvider.base_url = preset.defaultUrl;
		}
	}

	async function addProvider() {
		if (!newProvider.name.trim() || !newProvider.base_url.trim()) return;
		addingProvider = true;
		addError = '';
		try {
			const resp = await engineApi.addCustomProvider({
				name: newProvider.name.trim(),
				base_url: newProvider.base_url.trim(),
				provider_type: newProvider.provider_type,
				api_key: newProvider.api_key.trim() || undefined
			});
			customProviders = [...customProviders, resp.provider];
			newProvider = { name: '', base_url: '', provider_type: 'lmstudio', api_key: '' };
			showAddProvider = false;
			// Refresh models to include models from new provider
			await fetchModels(true);
		} catch (e: any) {
			addError = e.message || 'Failed to add provider';
		} finally {
			addingProvider = false;
		}
	}

	async function deleteProvider(providerId: string) {
		deletingProvider = providerId;
		try {
			await engineApi.deleteCustomProvider(providerId);
			customProviders = customProviders.filter(p => p.id !== providerId);
			delete testResults[providerId];
			delete providerModels[providerId];
			if (expandedProvider === providerId) expandedProvider = null;
			await fetchModels(true);
		} catch (e) {
			console.error('Failed to delete provider:', e);
		} finally {
			deletingProvider = null;
		}
	}

	async function testProvider(providerId: string) {
		testingProvider = providerId;
		try {
			const result = await engineApi.testCustomProvider(providerId);
			testResults[providerId] = result;
		} catch (e: any) {
			testResults[providerId] = {
				provider_id: providerId,
				reachable: false,
				models_count: 0,
				llm_count: 0,
				embedding_count: 0,
				inference_ok: false,
				latency_ms: 0,
				error: e.message || 'Connection failed'
			};
		} finally {
			testingProvider = null;
		}
	}

	async function toggleExpand(providerId: string) {
		if (expandedProvider === providerId) {
			expandedProvider = null;
			return;
		}
		expandedProvider = providerId;
		if (!providerModels[providerId]) {
			try {
				const resp = await engineApi.getProviderModels(providerId);
				providerModels[providerId] = resp.models;
			} catch (e) {
				console.error('Failed to fetch provider models:', e);
				providerModels[providerId] = [];
			}
		}
	}

	function startEdit(provider: CustomProvider) {
		editingProvider = provider.id;
		editForm = { name: provider.name, base_url: provider.base_url, api_key: '' };
	}

	async function saveEdit(providerId: string) {
		editSaving = true;
		try {
			const updates: Record<string, any> = {};
			if (editForm.name.trim()) updates.name = editForm.name.trim();
			if (editForm.base_url.trim()) updates.base_url = editForm.base_url.trim();
			if (editForm.api_key.trim()) updates.api_key = editForm.api_key.trim();
			const resp = await engineApi.updateCustomProvider(providerId, updates);
			customProviders = customProviders.map(p => p.id === providerId ? resp.provider : p);
			editingProvider = null;
			// Clear cached models for this provider and refresh
			delete providerModels[providerId];
			delete testResults[providerId];
			await fetchModels(true);
		} catch (e) {
			console.error('Failed to update provider:', e);
		} finally {
			editSaving = false;
		}
	}

	function getTypeLabel(type: string) {
		return providerTypes.find(p => p.id === type)?.label ?? type;
	}

	function debounceSavePipeline() {
		if (pipelineSaveTimer) clearTimeout(pipelineSaveTimer);
		pipelineSaveTimer = setTimeout(savePipeline, 600);
	}

	async function savePipeline() {
		savingPipeline = true;
		try {
			await engineApi.updateSettings({ pipeline } as any);
		} catch (e) {
			console.error('Failed to save pipeline settings:', e);
		} finally {
			savingPipeline = false;
		}
	}
</script>

{#if !loaded}
	<div class="flex items-center justify-center py-12 text-surface-400">Loading settings...</div>
{:else}
	<div class="space-y-8">
		<!-- API Keys -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
			<h3 class="mb-4 text-laya-heading font-medium">API Keys</h3>
			<p class="mb-4 text-laya-base text-surface-400">
				Keys are stored securely in your OS keychain. They are never sent to the UI.
			</p>
			<div class="space-y-4">
				{#each cloudProviders as provider}
					<div class="flex items-center gap-3">
						<div class="flex w-28 items-center gap-2">
							<span
								class="h-2 w-2 rounded-full {apiKeys[provider.id]
									? 'bg-green-500'
									: 'bg-surface-500'}"
							></span>
							<span class="text-laya-base text-surface-300">{provider.label}</span>
						</div>

						{#if apiKeys[provider.id]}
							<span class="text-laya-base text-green-400">Configured</span>
							<button
								onclick={() => removeApiKey(provider.id)}
								class="ml-auto text-laya-base text-red-400 transition-colors hover:text-red-300"
							>
								Remove
							</button>
						{:else}
							<input
								type="password"
								bind:value={keyInputs[provider.id]}
								placeholder="Enter API key..."
								class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-laya-base text-surface-100 placeholder:text-surface-500"
							/>
							<button
								onclick={() => saveApiKey(provider.id)}
								disabled={!keyInputs[provider.id].trim() || savingKey === provider.id}
								class="rounded-md bg-primary-600 px-3 py-1.5 text-laya-base font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
							>
								{savingKey === provider.id ? 'Saving...' : 'Save'}
							</button>
						{/if}
					</div>
				{/each}
			</div>
		</div>

		<!-- Local Providers -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
			<div class="mb-4 flex items-center justify-between">
				<div>
					<h3 class="mb-1 text-laya-heading font-medium">Local Providers</h3>
					<p class="text-laya-secondary text-surface-500">Connect to LM Studio, Ollama, or any OpenAI-compatible server running on your machine.</p>
				</div>
				<button
					onclick={() => { showAddProvider = !showAddProvider; addError = ''; }}
					class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-base text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-300"
				>
					{showAddProvider ? 'Cancel' : '+ Add Provider'}
				</button>
			</div>

			<!-- Add Provider Form -->
			{#if showAddProvider}
				<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="mb-5 rounded-md border border-surface-600 bg-surface-700/50 p-4 space-y-3">
					<div class="grid grid-cols-3 gap-3">
						{#each providerTypes as pt}
							<button
								onclick={() => handleProviderTypeChange(pt.id)}
								class="rounded-md border px-3 py-2 text-laya-base transition-colors
									{newProvider.provider_type === pt.id
										? 'border-laya-orange/50 bg-laya-orange/10 text-laya-orange'
										: 'border-surface-600 text-surface-400 hover:border-surface-500 hover:text-surface-300'}"
							>
								{pt.label}
							</button>
						{/each}
					</div>
					<div class="grid grid-cols-2 gap-3">
						<input
							type="text"
							bind:value={newProvider.name}
							placeholder="Display name (e.g. My LM Studio)"
							class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
						/>
						<input
							type="text"
							bind:value={newProvider.base_url}
							placeholder={providerTypes.find(p => p.id === newProvider.provider_type)?.defaultUrl ?? 'http://localhost:1234'}
							class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
						/>
					</div>
										<input
						type="password"
						bind:value={newProvider.api_key}
						placeholder="API key (optional — leave blank if not required)"
						class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
					/>
					{#if addError}
						<p class="text-laya-base text-red-400">{addError}</p>
					{/if}
					<div class="flex justify-end">
						<button
							onclick={addProvider}
							disabled={!newProvider.name.trim() || !newProvider.base_url.trim() || addingProvider}
							class="rounded-md bg-laya-orange px-4 py-2 text-laya-base font-medium text-surface-900 transition-colors hover:bg-laya-gold disabled:opacity-50"
						>
							{addingProvider ? 'Adding...' : 'Add Provider'}
						</button>
					</div>
				</div>
			{/if}

			<!-- Provider List -->
			{#if customProviders.length === 0 && !showAddProvider}
				<div class="rounded-md border border-dashed border-surface-600 py-8 text-center">
					<p class="text-laya-base text-surface-500">No local providers configured</p>
					<p class="mt-1 text-laya-secondary text-surface-600">Add LM Studio, Ollama, or another local server to use local models</p>
				</div>
			{:else}
				<div class="space-y-3">
					{#each customProviders as provider (provider.id)}
						<div class="rounded-md border border-surface-600 bg-surface-700/30">
							<!-- Provider header -->
							<div class="flex items-center gap-3 px-4 py-3">
								<button
									onclick={() => toggleExpand(provider.id)}
									class="flex flex-1 items-center gap-3 text-left"
								>
									<svg
										class="h-4 w-4 shrink-0 text-surface-400 transition-transform {expandedProvider === provider.id ? 'rotate-90' : ''}"
										fill="none" stroke="currentColor" viewBox="0 0 24 24"
									>
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
									</svg>
									<div class="min-w-0 flex-1">
										<div class="flex items-center gap-2">
											<span class="text-laya-base font-medium text-surface-200">{provider.name}</span>
											<span class="rounded-full bg-surface-600 px-2 py-0.5 text-laya-micro text-surface-400">
												{getTypeLabel(provider.provider_type)}
											</span>
											{#if testResults[provider.id]}
												{@const tr = testResults[provider.id]}
												<span class="h-2 w-2 rounded-full {tr.reachable ? (tr.inference_ok ? 'bg-green-500' : 'bg-yellow-500') : 'bg-red-500'}"></span>
											{/if}
										</div>
										<p class="mt-0.5 truncate text-laya-secondary text-surface-500">{provider.base_url}</p>
									</div>
								</button>

								<div class="flex items-center gap-1.5">
									<button
										onclick={() => testProvider(provider.id)}
										disabled={testingProvider === provider.id}
										class="rounded px-2 py-1 text-laya-secondary text-surface-400 transition-colors hover:bg-surface-600 hover:text-surface-300 disabled:opacity-50"
										title="Test connection"
									>
										{#if testingProvider === provider.id}
											<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
												<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
												<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
											</svg>
										{:else}
											Test
										{/if}
									</button>
									<button
										onclick={() => startEdit(provider)}
										class="rounded px-2 py-1 text-laya-secondary text-surface-400 transition-colors hover:bg-surface-600 hover:text-surface-300"
									>
										Edit
									</button>
									<button
										onclick={() => deleteProvider(provider.id)}
										disabled={deletingProvider === provider.id}
										class="rounded px-2 py-1 text-laya-secondary text-red-400/70 transition-colors hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
									>
										{deletingProvider === provider.id ? '...' : 'Remove'}
									</button>
								</div>
							</div>

							<!-- Test result -->
							{#if testResults[provider.id]}
								{@const tr = testResults[provider.id]}
								<div class="border-t border-surface-600/50 px-4 py-2 text-laya-secondary">
									{#if tr.reachable}
										<div class="flex items-center gap-4 text-surface-400">
											<span class="text-green-400">Connected</span>
											<span>{tr.models_count} model{tr.models_count !== 1 ? 's' : ''}</span>
											<span>Inference: <span class="{tr.inference_ok ? 'text-green-400' : 'text-yellow-400'}">{tr.inference_ok ? 'OK' : 'failed'}</span></span>
											<span>{tr.latency_ms}ms</span>
										</div>
									{:else}
										<span class="text-red-400">{tr.error || 'Unreachable'}</span>
									{/if}
								</div>
							{/if}

							<!-- Edit form -->
							{#if editingProvider === provider.id}
								<div class="border-t border-surface-600/50 px-4 py-3 space-y-3">
									<div class="grid grid-cols-2 gap-3">
										<input
											type="text"
											bind:value={editForm.name}
											placeholder="Display name"
											class="rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-laya-base text-surface-100 placeholder:text-surface-500"
										/>
										<input
											type="text"
											bind:value={editForm.base_url}
											placeholder="Base URL"
											class="rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-laya-base text-surface-100 placeholder:text-surface-500"
										/>
									</div>
									<input
										type="password"
										bind:value={editForm.api_key}
										placeholder="New API key (leave blank to keep current)"
										class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-laya-base text-surface-100 placeholder:text-surface-500"
									/>
									<div class="flex justify-end gap-2">
										<button
											onclick={() => { editingProvider = null; }}
											class="rounded-md px-3 py-1.5 text-laya-base text-surface-400 hover:text-surface-300"
										>
											Cancel
										</button>
										<button
											onclick={() => saveEdit(provider.id)}
											disabled={editSaving}
											class="rounded-md bg-laya-orange px-3 py-1.5 text-laya-base font-medium text-surface-900 transition-colors hover:bg-laya-gold disabled:opacity-50"
										>
											{editSaving ? 'Saving...' : 'Save'}
										</button>
									</div>
								</div>
							{/if}

							<!-- Expanded model list -->
							{#if expandedProvider === provider.id}
								<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="border-t border-surface-600/50 px-4 py-3">
									{#if !providerModels[provider.id]}
										<p class="text-laya-secondary text-surface-500">Loading models...</p>
									{:else if providerModels[provider.id].length === 0}
										<p class="text-laya-secondary text-surface-500">No models found. Is the server running?</p>
									{:else}
										<div class="space-y-1.5">
											{#each providerModels[provider.id] as model}
												<div class="flex items-center gap-3 rounded px-2 py-1.5 text-laya-secondary hover:bg-surface-700/50">
													<div class="flex items-center gap-1.5 min-w-0 flex-1">
														{#if model.loaded}
															<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" title="Loaded"></span>
														{:else}
															<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-surface-500" title="Not loaded"></span>
														{/if}
														<span class="truncate text-surface-200">{model.name}</span>
													</div>
													<div class="flex items-center gap-2 shrink-0 text-surface-500">
														{#if model.params}
															<span>{model.params}</span>
														{/if}
														{#if model.quantization}
															<span class="rounded bg-surface-600 px-1.5 py-0.5">{model.quantization}</span>
														{/if}
														{#if model.max_context_length}
															<span>{Math.round(model.max_context_length / 1024)}K ctx</span>
														{/if}
														{#if model.supports_tool_calling}
															<span class="rounded bg-blue-500/15 px-1.5 py-0.5 text-blue-400" title="Supports tool calling">tools</span>
														{/if}
														{#if model.supports_vision}
															<span class="rounded bg-purple-500/15 px-1.5 py-0.5 text-purple-400" title="Supports vision">vision</span>
														{/if}
													</div>
												</div>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Model Selection -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
			<div class="mb-4 flex items-center justify-between">
				<div>
					<h3 class="mb-1 text-laya-heading font-medium">Model Selection</h3>
					<p class="text-laya-secondary text-surface-500">Choose any model for each pipeline stage. Local provider models appear alongside cloud models.</p>
				</div>
				<div class="flex items-center gap-3">
					{#if saving}
						<span class="text-laya-micro text-laya-orange">Saving…</span>
					{/if}
				<button
					onclick={() => fetchModels(true)}
					disabled={modelsLoading}
					class="rounded-md border border-surface-600 px-2.5 py-1.5 text-laya-secondary text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-300 disabled:opacity-50"
					title="Refresh model list"
				>
					{#if modelsLoading}
						<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
						</svg>
					{:else}
						Refresh
					{/if}
				</button>
				</div>
			</div>
			<div class="space-y-4">
				{#each roles as role}
					<div class="grid grid-cols-[140px_auto_1fr] items-center gap-2">
						<div>
							<label for="{role.id}-model" class="text-laya-base text-surface-400">{role.label}</label>
							<p class="text-laya-micro text-surface-500">{role.hint}</p>
						</div>
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="cursor-help"
							onmouseenter={(e) => showGuide(e, role.guide)}
							onmouseleave={hideGuide}
						>
							<svg class="h-3.5 w-3.5 shrink-0 text-surface-600 transition-colors hover:text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01" />
								<circle cx="12" cy="12" r="10" stroke-width="2" />
							</svg>
						</div>
						<ModelSelect
							id="{role.id}-model"
							bind:value={models[role.id as keyof typeof models]}
							providers={availableModels}
							onchange={handleModelChange(role.id)}
						/>
					</div>
				{/each}
			</div>
		</div>

		<!-- Cost Control -->
		<div id="cost-control" class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
			<div class="mb-4">
				<div class="mb-1 flex items-center justify-between">
					<h3 class="text-laya-heading font-medium">Cost Control</h3>
					{#if savingBudget}
						<span class="text-laya-micro text-laya-orange">Saving…</span>
					{/if}
				</div>
				<p class="text-laya-secondary text-surface-500">Set a monthly budget to automatically pause workflows when the limit is reached.</p>
			</div>

			<!-- Budget paused alert -->
			{#if budgetIsPaused}
				<div class="mb-4 flex items-center justify-between rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3">
					<div class="flex items-center gap-2">
						<svg class="h-4 w-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
						</svg>
						<span class="text-laya-base text-red-300">
							Budget exceeded — {pausedWorkflowCount} workflow{pausedWorkflowCount !== 1 ? 's' : ''} paused
						</span>
					</div>
					<button
						onclick={handleResume}
						disabled={resumingBudget}
						class="rounded-md bg-red-500/20 px-3 py-1.5 text-laya-secondary font-medium text-red-300 transition-colors hover:bg-red-500/30 disabled:opacity-50"
					>
						{resumingBudget ? 'Resuming...' : 'Resume Workflows'}
					</button>
				</div>
			{/if}

			<!-- Enable toggle + limit input -->
			<div class="space-y-4">
				<div class="flex items-center gap-3">
					<button
						aria-label="Toggle monthly budget limit"
						onclick={() => { budgetEnabled = !budgetEnabled; debounceSaveBudget(); }}
						class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors {budgetEnabled ? 'bg-laya-orange' : 'bg-surface-600'}"
					>
						<span class="inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform {budgetEnabled ? 'translate-x-4' : 'translate-x-0.5'}"></span>
					</button>
					<span class="text-laya-base text-surface-300">Enable monthly budget limit</span>
				</div>

				{#if budgetEnabled}
					<div class="grid grid-cols-[200px_1fr] items-center gap-4">
						<div>
							<label for="budget-limit" class="text-laya-base text-surface-300">Monthly Limit</label>
							<p class="text-laya-micro text-surface-500">Workflows pause when this amount is reached.</p>
						</div>
						<div class="flex items-center gap-2">
							<span class="text-laya-base text-surface-400">$</span>
							<input
								id="budget-limit"
								type="number"
								min="0.01"
								step="0.50"
								placeholder="e.g. 10.00"
								bind:value={budgetLimitInput}
								oninput={debounceSaveBudget}
								class="w-32 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-laya-base text-surface-200 placeholder:text-surface-500"
							/>
							<span class="text-laya-secondary text-surface-500">USD / month</span>
						</div>
					</div>

					<!-- Current month progress -->
					<div class="rounded-lg border border-surface-700 bg-surface-900/50 p-4">
						<div class="mb-2 flex items-center justify-between">
							<span class="text-[13px] leading-5 text-surface-400">
								{currentMonth ? formatMonth(currentMonth) : 'Current Month'}
							</span>
							<span class="text-[13px] leading-5">
								<span class="font-semibold {budgetPercent >= 100 ? 'text-red-400' : budgetPercent >= 75 ? 'text-amber-400' : 'text-green-400'}">
									${currentMonthCost.toFixed(2)}
								</span>
								{#if budgetLimit}
									<span class="text-surface-500"> / ${budgetLimit.toFixed(2)}</span>
								{/if}
							</span>
						</div>
						<!-- Progress bar -->
						{#if budgetLimit}
							<div class="h-2 w-full overflow-hidden rounded-full bg-surface-700">
								<div
									class="h-full rounded-full transition-all duration-500 {budgetBarColor}"
									style="width: {budgetPercent}%"
								></div>
							</div>
							<div class="mt-1.5 text-right text-laya-micro text-surface-500">
								{budgetPercent.toFixed(0)}% used
							</div>
						{/if}

						<!-- Per-model breakdown -->
						{#if Object.keys(budgetByModel).length > 0}
							<div class="mt-3 space-y-1">
								{#each Object.entries(budgetByModel).sort((a, b) => b[1] - a[1]) as [model, cost]}
									<div class="flex items-center justify-between text-laya-secondary">
										<span class="truncate text-surface-400">{model}</span>
										<span class="shrink-0 text-surface-300">${cost.toFixed(4)}</span>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/if}

				<!-- History accordion -->
				<div>
					<button
						onclick={() => { showHistory = !showHistory; if (showHistory) loadHistory(); }}
						class="flex items-center gap-1.5 text-laya-secondary text-surface-400 transition-colors hover:text-surface-300"
					>
						<svg
							class="h-3.5 w-3.5 transition-transform {showHistory ? 'rotate-90' : ''}"
							fill="none" stroke="currentColor" viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
						</svg>
						Monthly History
					</button>
					{#if showHistory}
						<div class="mt-2 space-y-1.5">
							{#if historyLoading}
								<p class="text-laya-secondary text-surface-500">Loading...</p>
							{:else if budgetHistory.length === 0}
								<p class="text-laya-secondary text-surface-500">No cost history yet. History is recorded at the end of each month.</p>
							{:else}
								{#each budgetHistory as entry}
									<div class="flex items-center justify-between rounded-md border border-surface-700 bg-surface-900/30 px-3 py-2">
										<span class="text-laya-secondary text-surface-400">{formatMonth(entry.year_month)}</span>
										<span class="text-laya-secondary font-medium text-surface-200">${entry.total_cost_usd.toFixed(2)}</span>
									</div>
								{/each}
							{/if}
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Advanced Pipeline Settings -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'}">
			<button
				onclick={() => { showAdvanced = !showAdvanced; }}
				class="flex w-full items-center justify-between p-5 text-left"
			>
				<div>
					<h3 class="text-laya-heading font-medium">Advanced</h3>
					<p class="text-laya-secondary text-surface-500">Model timeout, retry attempts, and request concurrency</p>
				</div>
				{#if savingPipeline}
					<span class="text-laya-micro text-laya-orange">Saving…</span>
				{/if}
				<svg
					class="h-5 w-5 shrink-0 text-surface-400 transition-transform {showAdvanced ? 'rotate-180' : ''}"
					fill="none" stroke="currentColor" viewBox="0 0 24 24"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
				</svg>
			</button>

			{#if showAdvanced}
				<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="border-t border-surface-700 p-5 space-y-5">
					<!-- Model Timeout -->
					<div class="grid grid-cols-[200px_1fr_auto] items-center gap-4">
						<div>
							<label for="model-timeout" class="text-laya-base text-surface-300">Model Timeout</label>
							<p class="text-laya-micro text-surface-500">Max seconds to wait for an LLM response. Increase for slow local models.</p>
						</div>
						<input
							id="model-timeout"
							type="range"
							min="30"
							max="900"
							step="30"
							bind:value={pipeline.model_timeout}
							oninput={debounceSavePipeline}
							class="w-full accent-laya-orange"
						/>
						<div class="flex items-center gap-1.5">
							<input
								type="number"
								min="30"
								max="900"
								bind:value={pipeline.model_timeout}
								oninput={debounceSavePipeline}
								class="w-16 rounded-md border border-surface-600 bg-surface-700 px-2 py-1 text-center text-laya-base text-surface-200"
							/>
							<span class="text-laya-secondary text-surface-500">sec</span>
						</div>
					</div>

					<!-- LLM Retries (per call) -->
					<div class="grid grid-cols-[200px_1fr_auto] items-center gap-4">
						<div>
							<label for="llm-retries" class="text-laya-base text-surface-300">LLM Retries</label>
							<p class="text-laya-micro text-surface-500">Retries per LLM call on timeout or transient error (fast, seconds apart).</p>
						</div>
						<input
							id="llm-retries"
							type="range"
							min="1"
							max="5"
							step="1"
							bind:value={pipeline.llm_retries}
							oninput={debounceSavePipeline}
							class="w-full accent-laya-orange"
						/>
						<div class="flex items-center gap-1.5">
							<input
								type="number"
								min="1"
								max="5"
								bind:value={pipeline.llm_retries}
								oninput={debounceSavePipeline}
								class="w-16 rounded-md border border-surface-600 bg-surface-700 px-2 py-1 text-center text-laya-base text-surface-200"
							/>
							<span class="text-laya-secondary text-surface-500">tries</span>
						</div>
					</div>

					<!-- Event Queue Retries -->
					<div class="grid grid-cols-[200px_1fr_auto] items-center gap-4">
						<div>
							<label for="max-retries" class="text-laya-base text-surface-300">Event Queue Retries</label>
							<p class="text-laya-micro text-surface-500">Times a failed event is re-queued with exponential backoff (slow, minutes apart).</p>
						</div>
						<input
							id="max-retries"
							type="range"
							min="1"
							max="10"
							step="1"
							bind:value={pipeline.max_retry_attempts}
							oninput={debounceSavePipeline}
							class="w-full accent-laya-orange"
						/>
						<div class="flex items-center gap-1.5">
							<input
								type="number"
								min="1"
								max="10"
								bind:value={pipeline.max_retry_attempts}
								oninput={debounceSavePipeline}
								class="w-16 rounded-md border border-surface-600 bg-surface-700 px-2 py-1 text-center text-laya-base text-surface-200"
							/>
							<span class="text-laya-secondary text-surface-500">tries</span>
						</div>
					</div>

					<!-- Request Concurrency -->
					<div class="grid grid-cols-[200px_1fr_auto] items-center gap-4">
						<div>
							<label for="max-concurrent" class="text-laya-base text-surface-300">Request Concurrency</label>
							<p class="text-laya-micro text-surface-500">Max events processed in parallel. Lower for local GPU models.</p>
						</div>
						<input
							id="max-concurrent"
							type="range"
							min="1"
							max="20"
							step="1"
							bind:value={pipeline.max_concurrent_events}
							oninput={debounceSavePipeline}
							class="w-full accent-laya-orange"
						/>
						<div class="flex items-center gap-1.5">
							<input
								type="number"
								min="1"
								max="20"
								bind:value={pipeline.max_concurrent_events}
								oninput={debounceSavePipeline}
								class="w-16 rounded-md border border-surface-600 bg-surface-700 px-2 py-1 text-center text-laya-base text-surface-200"
							/>
							<span class="text-laya-secondary text-surface-500">events</span>
						</div>
					</div>

				</div>
			{/if}
		</div>
	</div>

	{#if guideTooltip}
		<div
			use:portal
			class="pointer-events-none fixed z-[100] w-64 -translate-x-1/2 rounded-lg border border-transparent glass-tooltip px-3 py-2.5 text-laya-secondary leading-relaxed shadow-lg"
			style="top: {guideTooltip.top}px; left: {guideTooltip.left}px;"
		>
			{guideTooltip.text}
		</div>
	{/if}
{/if}
