<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { invoke } from '@tauri-apps/api/core';
	import { engineApi } from '$lib/api/engine';
	import type { CustomProvider, DiscoveredModel } from '$lib/api/types';
	import { CODING_AGENTS } from '$lib/config';

	interface RepoDetection {
		path: string;
		name: string;
		platform: string;
		remote_id: string;
	}

	$effect(() => {
		if (step === 3 && n8nStatus === 'checking') {
			checkN8n();
		}
	});

	let step = $state(1);
	const totalSteps = 6;

	// Step 1: API Key
	let provider = $state('anthropic');
	let apiKey = $state('');
	let keyStatus = $state('');
	let savedKeys = $state<Array<{ provider: string; label: string }>>([]);
	let useSelfHosted = $state(false);

	const providerLabels: Record<string, string> = {
		anthropic: 'Anthropic (Claude)',
		openai: 'OpenAI',
		google: 'Google (Gemini)'
	};

	// Provider-to-model defaults: small model for router, large model for stager/chat/trace
	const providerModelDefaults: Record<string, Record<string, string>> = {
		anthropic: { router: 'claude-haiku-4-5', stager: 'claude-sonnet-4-6', chat: 'claude-sonnet-4-6', trace: 'claude-sonnet-4-6', omni: 'claude-sonnet-4-6' },
		openai: { router: 'gpt-4o-mini', stager: 'gpt-4o', chat: 'gpt-4o', trace: 'gpt-4o', omni: 'gpt-4o' },
		google: { router: 'gemini-2.0-flash', stager: 'gemini-2.5-pro', chat: 'gemini-2.5-pro', trace: 'gemini-2.5-pro', omni: 'gemini-2.5-pro' }
	};

	// Step 2: Model defaults
	let defaultProvider = $state('');
	let settingDefaults = $state(false);

	// Self-hosted provider config (step 2)
	let selfHostedProviderType = $state('lmstudio');
	let selfHostedName = $state('');
	let selfHostedUrl = $state('http://localhost:1234');
	let selfHostedApiKey = $state('');
	let addingSelfHosted = $state(false);
	let selfHostedError = $state('');
	let selfHostedProvider = $state<CustomProvider | null>(null);
	let selfHostedTesting = $state(false);
	let selfHostedTestOk = $state<boolean | null>(null);
	let selfHostedModels = $state<DiscoveredModel[]>([]);
	let selfHostedModelsLoading = $state(false);
	let selfHostedModelSelections = $state<Record<string, string>>({ router: '', stager: '', chat: '', trace: '', omni: '' });

	const selfHostedProviderTypes = [
		{ id: 'lmstudio', label: 'LM Studio', defaultUrl: 'http://localhost:1234' },
		{ id: 'ollama', label: 'Ollama', defaultUrl: 'http://localhost:11434' },
		{ id: 'openai_compatible', label: 'OpenAI Compatible', defaultUrl: 'http://localhost:8080' }
	];

	const roles = [
		{ id: 'router', label: 'Router', hint: 'Classifies incoming events (use a fast/cheap model)' },
		{ id: 'stager', label: 'Stager', hint: 'Synthesises action cards (use a capable model)' },
		{ id: 'chat', label: 'Chat', hint: 'Conversational responses' },
		{ id: 'trace', label: 'Coherence', hint: 'Generates trace narratives' },
		{ id: 'omni', label: 'Omni', hint: 'Resynthesises rolling summaries' }
	];

	// Derived: whether step 2 needs to show provider picker
	const needsProviderChoice = $derived(
		(savedKeys.length > 1 && !useSelfHosted) ||
		(savedKeys.length >= 1 && useSelfHosted)
	);
	const singleCloudProvider = $derived(savedKeys.length === 1 && !useSelfHosted);
	const selfHostedOnly = $derived(savedKeys.length === 0 && useSelfHosted);

	// Whether self-hosted is fully configured (provider added + all roles selected)
	const selfHostedConfigured = $derived(
		selfHostedProvider !== null &&
		selfHostedModelSelections.router !== '' &&
		selfHostedModelSelections.stager !== '' &&
		selfHostedModelSelections.chat !== '' &&
		selfHostedModelSelections.trace !== '' &&
		selfHostedModelSelections.omni !== ''
	);

	// Whether step 2 can proceed
	const step2CanProceed = $derived(() => {
		if (selfHostedOnly) {
			return selfHostedConfigured;
		}
		if (needsProviderChoice) {
			if (defaultProvider === 'self-hosted') {
				return selfHostedConfigured;
			}
			return defaultProvider !== '';
		}
		// Single cloud provider — auto-set, always can proceed
		return true;
	});

	// Step 4: Coding Agent + Repo
	let codingAgent = $state('claude_code');
	let repoName = $state('');
	let repoPath = $state('');
	let repoPlatform = $state('');
	let repoRemoteId = $state('');
	let repoBrowseStatus = $state<{ ok: boolean; msg: string } | null>(null);
	let browsing = $state(false);
	let savedRepos = $state<Array<{ name: string; path: string; platform: string; remote_id: string }>>([]);
	let showManualRepo = $state(false);

	// Step 5: Team
	let members = $state<Array<{ name: string; email: string; role: 'self' | 'manager' | 'teammate' | 'external' | 'bot' }>>([
		{ name: '', email: '', role: 'teammate' }
	]);

	// Step 3: n8n
	let n8nStatus = $state<'checking' | 'running' | 'not_running' | 'configured'>('checking');
	let bootstrapping = $state(false);
	let bootstrapMessage = $state('');

	// Step 6: Filters
	let ignoreBots = $state(true);
	let muteRandom = $state(false);

	async function saveApiKey() {
		if (!apiKey.trim()) return;
		try {
			await engineApi.setApiKey(provider, apiKey.trim());
			savedKeys = [...savedKeys.filter((k) => k.provider !== provider), { provider, label: providerLabels[provider] || provider }];
			apiKey = '';
			keyStatus = '';
		} catch {
			keyStatus = 'error';
		}
	}

	async function applyModelDefaults(providerKey: string) {
		const defaults = providerModelDefaults[providerKey];
		if (!defaults) return;
		settingDefaults = true;
		try {
			await engineApi.updateSettings({ models: defaults } as any);
		} catch (e) {
			console.error('Failed to set model defaults:', e);
		} finally {
			settingDefaults = false;
		}
	}

	async function applySelfHostedModels() {
		settingDefaults = true;
		try {
			await engineApi.updateSettings({
				models: {
					router: selfHostedModelSelections.router,
					stager: selfHostedModelSelections.stager,
					chat: selfHostedModelSelections.chat,
					trace: selfHostedModelSelections.trace,
					omni: selfHostedModelSelections.omni
				}
			} as any);
		} catch (e) {
			console.error('Failed to set self-hosted model defaults:', e);
		} finally {
			settingDefaults = false;
		}
	}

	function handleSelfHostedTypeChange(type: string) {
		selfHostedProviderType = type;
		const preset = selfHostedProviderTypes.find(p => p.id === type);
		if (preset) {
			selfHostedUrl = preset.defaultUrl;
		}
	}

	async function addSelfHostedProvider() {
		if (!selfHostedName.trim() || !selfHostedUrl.trim()) return;
		addingSelfHosted = true;
		selfHostedError = '';
		try {
			const resp = await engineApi.addCustomProvider({
				name: selfHostedName.trim(),
				base_url: selfHostedUrl.trim(),
				provider_type: selfHostedProviderType,
				api_key: selfHostedApiKey.trim() || undefined
			});
			selfHostedProvider = resp.provider;
			// Auto-test and discover models
			await testAndDiscoverModels(resp.provider.id);
		} catch (e: any) {
			selfHostedError = e.message || 'Failed to add provider';
		} finally {
			addingSelfHosted = false;
		}
	}

	async function testAndDiscoverModels(providerId: string) {
		selfHostedTesting = true;
		selfHostedTestOk = null;
		try {
			const result = await engineApi.testCustomProvider(providerId);
			selfHostedTestOk = result.reachable;
			if (result.reachable) {
				selfHostedModelsLoading = true;
				const modelsResp = await engineApi.getProviderModels(providerId);
				selfHostedModels = modelsResp.models.filter(m => m.type === 'llm');
				selfHostedModelsLoading = false;
			}
		} catch (e) {
			selfHostedTestOk = false;
			console.error('Failed to test provider:', e);
		} finally {
			selfHostedTesting = false;
		}
	}

	async function goToStep2() {
		// If single cloud provider and no self-hosted, auto-set defaults and skip to step 3
		if (singleCloudProvider) {
			await applyModelDefaults(savedKeys[0].provider);
			step = 3;
			return;
		}
		// If no keys and no self-hosted, skip model config entirely
		if (savedKeys.length === 0 && !useSelfHosted) {
			step = 3;
			return;
		}
		// Pre-select default provider if only one cloud key (with self-hosted also available)
		if (savedKeys.length === 1 && useSelfHosted) {
			defaultProvider = '';
		} else if (savedKeys.length > 1) {
			defaultProvider = '';
		}
		step = 2;
	}

	async function finishStep2() {
		if (selfHostedOnly || defaultProvider === 'self-hosted') {
			await applySelfHostedModels();
		} else if (defaultProvider) {
			await applyModelDefaults(defaultProvider);
		}
		step = 3;
	}

	async function checkN8n() {
		n8nStatus = 'checking';
		try {
			const result = await engineApi.testN8nConnection();
			if (result.health === 'healthy') {
				const settings = await engineApi.getSettings();
				n8nStatus = settings.api_keys?.n8n ? 'configured' : 'running';
				if (n8nStatus === 'running') {
					await doBootstrap();
				}
			} else {
				n8nStatus = 'not_running';
			}
		} catch {
			n8nStatus = 'not_running';
		}
	}

	async function doBootstrap() {
		bootstrapping = true;
		bootstrapMessage = '';
		try {
			const result = await engineApi.bootstrapN8n();
			bootstrapMessage = result.message;
			n8nStatus = result.has_api_key ? 'configured' : 'running';
		} catch {
			bootstrapMessage = 'Failed to auto-configure n8n';
		} finally {
			bootstrapping = false;
		}
	}

	async function browseRepo() {
		browsing = true;
		repoBrowseStatus = null;
		try {
			const result = await invoke<RepoDetection>('pick_repo_folder');
			savedRepos = [...savedRepos, {
				name: result.name,
				path: result.path,
				platform: result.platform || 'github',
				remote_id: result.remote_id
			}];
		} catch (err: unknown) {
			const msg = String(err);
			if (!msg.includes('cancelled')) {
				repoBrowseStatus = { ok: false, msg: msg.replace(/^Error: /, '') };
			}
		} finally {
			browsing = false;
		}
	}

	function addRepo() {
		if (!repoName.trim() || !repoPath.trim()) return;
		savedRepos = [...savedRepos, {
			name: repoName.trim(),
			path: repoPath.trim(),
			platform: repoPlatform || 'github',
			remote_id: repoRemoteId
		}];
		repoName = '';
		repoPath = '';
		repoPlatform = '';
		repoRemoteId = '';
		repoBrowseStatus = null;
	}

	function removeRepo(index: number) {
		savedRepos = savedRepos.filter((_, i) => i !== index);
	}

	function addMember() {
		members = [...members, { name: '', email: '', role: 'teammate' }];
	}

	function removeMember(index: number) {
		members = members.filter((_, i) => i !== index);
	}

	async function finish() {
		// Step 4: Save agent + repos
		await engineApi.updateSettings({ coding_agent: codingAgent });
		const allRepos = [...savedRepos];
		if (repoName.trim() && repoPath.trim()) {
			allRepos.push({ name: repoName.trim(), path: repoPath.trim(), platform: repoPlatform || 'github', remote_id: repoRemoteId });
		}
		if (allRepos.length > 0) {
			await engineApi.updateRepos({ repos: allRepos });
		}

		// Step 5: Save team
		const validMembers = members.filter((m) => m.name.trim() && m.email.trim());
		if (validMembers.length > 0) {
			await engineApi.updateTeam({
				members: validMembers.map((m) => ({
					name: m.name.trim(),
					email: m.email.trim(),
					role: m.role,
					notes: '',
					aliases: [],
					accounts: []
				}))
			});
		}

		// Step 6: Save filter rules
		const rules: Array<import('$lib/api/types').Rule> = [];
		if (ignoreBots) {
			rules.push({
				name: 'Ignore bot messages',
				enabled: true,
				condition: { field: 'actor.email', operator: 'contains', value: 'bot' },
				action: 'drop'
			});
		}
		if (muteRandom) {
			rules.push({
				name: 'Mute #random',
				enabled: true,
				condition: {
					all: [
						{ field: 'source.platform', operator: 'equals', value: 'slack' },
						{
							field: 'content.metadata.slack_channel',
							operator: 'equals',
							value: 'random'
						}
					]
				},
				action: 'drop'
			});
		}
		if (rules.length > 0) {
			await engineApi.updateRules({ rules });
		}

		await engineApi.updateSettings({ setup_complete: true });
		goto('/');
	}

	function handleNext() {
		if (step === 1) {
			goToStep2();
		} else if (step === 2) {
			finishStep2();
		} else {
			step += 1;
		}
	}
</script>

<div class="max-h-[calc(100vh-4rem)] overflow-y-auto space-y-6 rounded-xl border border-surface-700 bg-surface-800 p-8">
	<!-- Step indicator -->
	<div class="flex items-center justify-center gap-2">
		{#each Array(totalSteps) as _, i}
			<div
				class="h-2 w-8 rounded-full transition-colors
					{i + 1 <= step ? 'bg-primary-500' : 'bg-surface-600'}"
			></div>
		{/each}
	</div>

	<!-- Step 1: Welcome + API Key -->
	{#if step === 1}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Welcome to Laya</h2>
			<p class="text-sm text-surface-400">
				Let's get you set up. First, add an API key for your LLM provider.
			</p>

			<!-- Saved keys -->
			{#if savedKeys.length > 0}
				<div class="space-y-1.5">
					{#each savedKeys as key}
						<div class="flex items-center gap-2 rounded-md border border-green-500/20 bg-green-500/5 px-3 py-2">
							<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
							<span class="text-sm text-surface-200">{key.label}</span>
							<span class="text-xs text-green-400">saved</span>
						</div>
					{/each}
				</div>
			{/if}

			<div class="space-y-3">
				<label class="block text-sm font-medium">
					Provider
					<select
						class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
						bind:value={provider}
					>
						<option value="anthropic">Anthropic (Claude)</option>
						<option value="openai">OpenAI</option>
						<option value="google">Google (Gemini)</option>
					</select>
				</label>

				<label class="block text-sm font-medium">
					API Key
					<input
						type="password"
						class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
						placeholder="sk-..."
						bind:value={apiKey}
					/>
				</label>

				<button
					class="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
					onclick={saveApiKey}
					disabled={!apiKey.trim()}
				>
					Save Key
				</button>

				{#if keyStatus === 'error'}
					<p class="text-sm text-red-400">Failed to save key</p>
				{/if}
			</div>

			<!-- Self-hosted toggle -->
			<div class="border-t border-surface-700 pt-4">
				<label class="flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors
					{useSelfHosted
					? 'border-laya-orange bg-laya-orange/10'
					: 'border-surface-600 bg-surface-800 hover:border-surface-500'}">
					<input type="checkbox" class="accent-laya-orange" bind:checked={useSelfHosted} />
					<div>
						<div class="text-sm font-medium">I want to use a self-hosted model</div>
						<div class="text-xs text-surface-400">Configure a local provider like Ollama, LM Studio, or any OpenAI-compatible server</div>
					</div>
				</label>
			</div>
		</div>

	<!-- Step 2: Configure Default Models -->
	{:else if step === 2}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Configure Default Models</h2>

			<!-- Provider choice (when multiple options exist) -->
			{#if needsProviderChoice}
				<p class="text-sm text-surface-400">
					You have multiple providers available. Choose which one to use as the default for Laya's pipeline.
				</p>

				<div class="space-y-2">
					{#each savedKeys as key}
						<label
							class="flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors
								{defaultProvider === key.provider
								? 'border-laya-orange bg-laya-orange/10'
								: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
						>
							<input type="radio" bind:group={defaultProvider} value={key.provider} class="accent-laya-orange" />
							<div>
								<div class="text-sm font-medium">{key.label}</div>
								<div class="text-xs text-surface-400">
									{#if providerModelDefaults[key.provider]}
										Router: {providerModelDefaults[key.provider].router} / Others: {providerModelDefaults[key.provider].stager}
									{/if}
								</div>
							</div>
						</label>
					{/each}

					{#if useSelfHosted}
						<label
							class="flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors
								{defaultProvider === 'self-hosted'
								? 'border-laya-orange bg-laya-orange/10'
								: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
						>
							<input type="radio" bind:group={defaultProvider} value="self-hosted" class="accent-laya-orange" />
							<div>
								<div class="text-sm font-medium">Self-hosted model</div>
								<div class="text-xs text-surface-400">Use a locally running model server</div>
							</div>
						</label>
					{/if}
				</div>
			{:else if selfHostedOnly}
				<p class="text-sm text-surface-400">
					Set up your self-hosted model provider and select models for each pipeline role.
				</p>
			{/if}

			<!-- Self-hosted provider setup (shown when self-hosted is the choice) -->
			{#if selfHostedOnly || defaultProvider === 'self-hosted'}
				<div class="space-y-4 rounded-lg border border-surface-600 bg-surface-900/50 p-4">
					{#if !selfHostedProvider}
						<h3 class="text-sm font-semibold text-surface-200">Add Local Provider</h3>

						<div class="space-y-3">
							<label class="block text-sm font-medium">
								Provider Type
								<select
									class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
									value={selfHostedProviderType}
									onchange={(e) => handleSelfHostedTypeChange((e.target as HTMLSelectElement).value)}
								>
									{#each selfHostedProviderTypes as pt}
										<option value={pt.id}>{pt.label}</option>
									{/each}
								</select>
							</label>

							<label class="block text-sm font-medium">
								Name
								<input
									type="text"
									class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
									placeholder="My Local Server"
									bind:value={selfHostedName}
								/>
							</label>

							<label class="block text-sm font-medium">
								Base URL
								<input
									type="text"
									class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
									bind:value={selfHostedUrl}
								/>
							</label>

							<label class="block text-sm font-medium">
								API Key <span class="text-xs text-surface-500">(optional)</span>
								<input
									type="password"
									class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
									placeholder="Leave blank if not required"
									bind:value={selfHostedApiKey}
								/>
							</label>

							<button
								class="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
								onclick={addSelfHostedProvider}
								disabled={!selfHostedName.trim() || !selfHostedUrl.trim() || addingSelfHosted}
							>
								{addingSelfHosted ? 'Adding...' : 'Add & Discover Models'}
							</button>

							{#if selfHostedError}
								<p class="text-sm text-red-400">{selfHostedError}</p>
							{/if}
						</div>
					{:else}
						<!-- Provider added — show status and model selection -->
						<div class="flex items-center gap-3">
							<span class="h-2.5 w-2.5 rounded-full {selfHostedTestOk ? 'bg-green-500' : selfHostedTestOk === false ? 'bg-red-500' : 'bg-surface-500 animate-pulse'}"></span>
							<div>
								<span class="text-sm font-medium">{selfHostedProvider.name}</span>
								<span class="ml-2 text-xs text-surface-400">{selfHostedProvider.base_url}</span>
							</div>
							{#if selfHostedTesting}
								<span class="text-xs text-surface-400">Testing...</span>
							{:else if selfHostedTestOk === false}
								<button
									class="text-xs text-primary-400 hover:text-primary-300"
									onclick={() => selfHostedProvider && testAndDiscoverModels(selfHostedProvider.id)}
								>
									Retry
								</button>
							{/if}
						</div>

						{#if selfHostedTestOk === false}
							<p class="text-sm text-red-400">Could not connect to provider. Make sure it's running and try again.</p>
						{/if}

						{#if selfHostedModelsLoading}
							<p class="text-sm text-surface-400">Discovering models...</p>
						{:else if selfHostedModels.length > 0}
							<h3 class="text-sm font-semibold text-surface-200 pt-2">Select models for each pipeline role</h3>
							<div class="space-y-3">
								{#each roles as role}
									<label class="block text-sm font-medium">
										{role.label}
										<span class="text-xs font-normal text-surface-400 ml-1">— {role.hint}</span>
										<select
											class="mt-1 block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
											bind:value={selfHostedModelSelections[role.id]}
										>
											<option value="">Select a model...</option>
											{#each selfHostedModels as model}
												<option value={model.id}>{model.name}{model.params ? ` (${model.params})` : ''}{model.quantization ? ` [${model.quantization}]` : ''}</option>
											{/each}
										</select>
									</label>
								{/each}
							</div>
						{:else if selfHostedTestOk}
							<p class="text-sm text-yellow-400">No LLM models found on this provider. Make sure you have models loaded.</p>
						{/if}
					{/if}
				</div>
			{/if}

			<!-- Single cloud provider confirmation -->
			{#if !needsProviderChoice && !selfHostedOnly && savedKeys.length === 1}
				<p class="text-sm text-surface-400">
					Setting up defaults for <strong class="text-surface-200">{savedKeys[0].label}</strong>...
				</p>
			{/if}
		</div>

	<!-- Step 3: Connect Tools -->
	{:else if step === 3}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Connect Tools</h2>
			<p class="text-sm text-surface-400">
				Laya uses n8n to connect to your tools (Jira, Slack, GitHub, etc.). Let's make sure
				it's ready.
			</p>

			<div class="rounded-md border border-surface-600 bg-surface-700 p-4">
				<div class="flex items-center gap-3">
					<span
						class="h-3 w-3 rounded-full
							{n8nStatus === 'configured'
							? 'bg-green-500'
							: n8nStatus === 'running'
								? 'bg-yellow-500'
								: n8nStatus === 'not_running'
									? 'bg-red-500'
									: 'bg-surface-500 animate-pulse'}"
					></span>
					<span class="text-sm font-medium">
						{#if n8nStatus === 'configured'}
							n8n is connected and ready
						{:else if n8nStatus === 'running'}
							n8n is running
						{:else if n8nStatus === 'not_running'}
							n8n is not running
						{:else}
							Checking n8n...
						{/if}
					</span>
				</div>

				{#if bootstrapMessage}
					<p
						class="mt-2 text-sm
							{n8nStatus === 'configured' ? 'text-green-400' : 'text-yellow-400'}"
					>
						{bootstrapMessage}
					</p>
				{/if}

				{#if n8nStatus === 'not_running'}
					<p class="mt-3 text-sm text-surface-400">
						n8n will start automatically when the app launches. Click retry to check again.
					</p>
					<button
						onclick={checkN8n}
						class="mt-2 rounded-md bg-surface-600 px-4 py-2 text-sm font-medium transition-colors hover:bg-surface-500"
					>
						Retry
					</button>
				{:else if n8nStatus === 'running' && !bootstrapping}
					<button
						onclick={doBootstrap}
						class="mt-3 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500"
					>
						Auto-configure
					</button>
				{:else if bootstrapping}
					<p class="mt-3 text-sm text-surface-400">Setting up n8n...</p>
				{/if}
			</div>

			<p class="text-xs text-surface-500">
				You can skip this and configure integrations later in Settings.
			</p>
		</div>

	<!-- Step 4: Coding Agent + Repo -->
	{:else if step === 4}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Coding Agent (optional)</h2>
			<p class="text-sm text-surface-400">Choose a coding agent for automated code tasks, or skip if you don't need one.</p>

			<div class="space-y-2">
				{#each CODING_AGENTS as option}
					<label
						class="flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors
							{codingAgent === option.value
							? 'border-laya-orange bg-laya-orange/10'
							: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
					>
						<input type="radio" bind:group={codingAgent} value={option.value} class="accent-laya-orange" />
						<div>
							<div class="text-sm font-medium">{option.label}</div>
							<div class="text-xs text-surface-400">{option.description}</div>
						</div>
					</label>
				{/each}
			</div>

			<div class="space-y-3 pt-2">
				<p class="text-sm font-medium">Repositories (optional)</p>

				{#if savedRepos.length > 0}
					<div class="max-h-32 space-y-1.5 overflow-y-auto pr-1">
						{#each savedRepos as repo, i}
							<div class="flex items-center gap-2 rounded-md border border-green-500/20 bg-green-500/5 px-3 py-1.5">
								<span class="h-1.5 w-1.5 rounded-full bg-green-500 shrink-0"></span>
								<span class="flex-1 min-w-0 truncate text-sm text-surface-200">{repo.name}</span>
								<span class="text-xs text-surface-500">{repo.platform}</span>
								<button
									class="shrink-0 text-xs text-red-400 hover:text-red-300"
									onclick={() => removeRepo(i)}
								>
									Remove
								</button>
							</div>
						{/each}
					</div>
				{/if}

				<div class="flex items-center gap-3">
					<button
						class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm font-medium transition-colors hover:bg-surface-600 disabled:opacity-50"
						onclick={browseRepo}
						disabled={browsing}
					>
						{browsing ? 'Opening...' : 'Browse...'}
					</button>
					{#if repoBrowseStatus && !repoBrowseStatus.ok}
						<span class="text-sm text-red-400">{repoBrowseStatus.msg}</span>
					{/if}
					{#if !showManualRepo}
						<button
							class="text-xs text-surface-500 hover:text-surface-300"
							onclick={() => showManualRepo = true}
						>
							or add manually
						</button>
					{/if}
				</div>

				{#if showManualRepo}
					<div class="space-y-2 rounded-md border border-surface-700 bg-surface-800/50 p-3">
						<input
							type="text"
							class="block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
							placeholder="Repository name"
							bind:value={repoName}
						/>
						<input
							type="text"
							class="block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm"
							placeholder="/path/to/repo"
							bind:value={repoPath}
						/>
						<div class="flex items-center gap-2">
							<button
								class="rounded-md bg-surface-600 px-3 py-1.5 text-sm font-medium text-surface-200 transition-colors hover:bg-surface-500 disabled:opacity-50"
								onclick={addRepo}
								disabled={!repoName.trim() || !repoPath.trim()}
							>
								Add
							</button>
							<button
								class="text-xs text-surface-500 hover:text-surface-300"
								onclick={() => { showManualRepo = false; repoName = ''; repoPath = ''; }}
							>
								Cancel
							</button>
						</div>
					</div>
				{/if}
			</div>
		</div>

	<!-- Step 5: Team Members -->
	{:else if step === 5}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Team Members</h2>
			<p class="text-sm text-surface-400">Add people Laya should recognize in events.</p>

			<div class="space-y-3">
				{#each members as member, i}
					<div class="flex items-start gap-2">
						<div class="flex-1 space-y-1">
							<input
								type="text"
								class="block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm"
								placeholder="Name"
								bind:value={member.name}
							/>
							<input
								type="email"
								class="block w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm"
								placeholder="Email"
								bind:value={member.email}
							/>
						</div>
						<select
							class="rounded-md border border-surface-600 bg-surface-700 px-2 py-1.5 text-sm"
							bind:value={member.role}
						>
							<option value="teammate">Teammate</option>
							<option value="manager">Manager</option>
							<option value="stakeholder">Stakeholder</option>
							<option value="bot">Bot</option>
						</select>
						{#if members.length > 1}
							<button
								class="rounded-md px-2 py-1.5 text-sm text-red-400 hover:text-red-300"
								onclick={() => removeMember(i)}
							>
								X
							</button>
						{/if}
					</div>
				{/each}
			</div>

			<button
				class="text-sm text-primary-400 hover:text-primary-300"
				onclick={addMember}
			>
				+ Add member
			</button>
		</div>

	<!-- Step 6: Filter Presets -->
	{:else if step === 6}
		<div class="space-y-4">
			<h2 class="text-xl font-semibold">Filters</h2>
			<p class="text-sm text-surface-400">Choose which events to filter out automatically.</p>

			<div class="space-y-3">
				<label class="flex items-center gap-3 rounded-md border border-surface-600 p-3">
					<input type="checkbox" class="accent-laya-orange" bind:checked={ignoreBots} />
					<div>
						<div class="text-sm font-medium">Ignore bot messages</div>
						<div class="text-xs text-surface-400">Filter events from CI bots, webhooks, etc.</div>
					</div>
				</label>

				<label class="flex items-center gap-3 rounded-md border border-surface-600 p-3">
					<input type="checkbox" class="accent-laya-orange" bind:checked={muteRandom} />
					<div>
						<div class="text-sm font-medium">Mute #random</div>
						<div class="text-xs text-surface-400">Filter Slack messages from #random channel</div>
					</div>
				</label>
			</div>
		</div>
	{/if}

	<!-- Navigation -->
	<div class="flex justify-between pt-2">
		{#if step > 1}
			<button
				class="rounded-md bg-surface-600 px-4 py-2 text-sm font-medium text-surface-200 transition-colors hover:bg-surface-500"
				onclick={() => (step -= 1)}
			>
				Back
			</button>
		{:else}
			<div></div>
		{/if}

		{#if step < totalSteps}
			<button
				class="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
				onclick={handleNext}
				disabled={step === 2 && !step2CanProceed()}
			>
				{#if step === 1 && savedKeys.length === 0 && !useSelfHosted}
					Skip
				{:else if step === 2 && settingDefaults}
					Saving...
				{:else}
					Next
				{/if}
			</button>
		{:else}
			<button
				class="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-500"
				onclick={finish}
			>
				Finish Setup
			</button>
		{/if}
	</div>
</div>
