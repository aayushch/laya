<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import PlatformIcon from './PlatformIcon.svelte';
	import type {
		N8nTestResult,
		N8nBootstrapResponse,
		AvailableWorkflow,
		Source
	} from '$lib/api/types';

	const DEFAULT_WEBHOOKS: Record<string, string> = {
		jira: 'jira-executor',
		bitbucket: 'bitbucket-executor',
		slack: 'slack-executor',
		gmail: 'gmail-executor',
		calendar: 'calendar-executor'
	};

	// Loading / error
	let loading = $state(true);
	let error = $state<string | null>(null);

	// n8n connection
	let baseUrl = $state('http://localhost:45678');
	let n8nHealth = $state<string | null>(null);
	let hasN8nKey = $state(false);
	let showAdvanced = $state(false);

	// Bootstrap
	let bootstrapping = $state(false);
	let bootstrapResult = $state<N8nBootstrapResponse | null>(null);

	// API key manual entry (fallback)
	let n8nApiKey = $state('');
	let savingApiKey = $state(false);
	let showManualKeyInput = $state(false);

	// Workflows & sources
	let workflows = $state<AvailableWorkflow[]>([]);
	let sources = $state<Source[]>([]);
	let loadingWorkflows = $state(false);
	let workflowError = $state<string | null>(null);

	// Webhook mappings
	let webhooks = $state<Record<string, string>>({ ...DEFAULT_WEBHOOKS });
	let showWebhooks = $state(false);
	let saving = $state(false);
	let showAddWebhook = $state(false);
	let newPlatform = $state('');
	let newPath = $state('');
	let editingPlatform = $state<string | null>(null);
	let editPath = $state('');
	let testResult = $state<N8nTestResult | null>(null);
	let testing = $state(false);

	onMount(async () => {
		try {
			const settings = await engineApi.getSettings();
			if (settings.n8n) {
				baseUrl = settings.n8n.base_url || baseUrl;
				webhooks = { ...DEFAULT_WEBHOOKS, ...settings.n8n.webhooks };
			}
			hasN8nKey = settings.api_keys?.n8n ?? false;

			checkHealth();

			if (hasN8nKey) {
				await loadWorkflows();
			}
		} catch {
			error = 'Failed to load settings';
		} finally {
			loading = false;
		}
	});

	async function checkHealth() {
		try {
			const result = await engineApi.testN8nConnection(baseUrl);
			n8nHealth = result.health;
		} catch {
			n8nHealth = 'error';
		}
	}

	async function loadWorkflows() {
		loadingWorkflows = true;
		workflowError = null;
		try {
			const [wfResp, srcResp] = await Promise.all([
				engineApi.getAvailableWorkflows(),
				engineApi.getSources()
			]);
			workflows = wfResp.workflows;
			sources = srcResp.sources;
		} catch (e) {
			workflowError = e instanceof Error ? e.message : 'Failed to load workflows';
		} finally {
			loadingWorkflows = false;
		}
	}

	async function doBootstrap() {
		bootstrapping = true;
		bootstrapResult = null;
		error = null;
		try {
			bootstrapResult = await engineApi.bootstrapN8n();
			hasN8nKey = bootstrapResult.has_api_key;
			if (hasN8nKey) {
				await loadWorkflows();
			}
			await checkHealth();
		} catch {
			bootstrapResult = {
				status: 'error',
				message: 'Failed to connect to engine',
				has_api_key: false
			};
		} finally {
			bootstrapping = false;
		}
	}

	async function saveN8nApiKey() {
		if (!n8nApiKey.trim()) return;
		savingApiKey = true;
		error = null;
		try {
			await engineApi.setApiKey('n8n', n8nApiKey.trim());
			hasN8nKey = true;
			n8nApiKey = '';
			showManualKeyInput = false;
			await loadWorkflows();
		} catch {
			error = 'Failed to save n8n API key';
		} finally {
			savingApiKey = false;
		}
	}

	async function removeN8nApiKey() {
		try {
			await engineApi.deleteApiKey('n8n');
			hasN8nKey = false;
			workflows = [];
			sources = [];
			bootstrapResult = null;
		} catch {
			error = 'Failed to remove n8n API key';
		}
	}

	// Webhook helpers
	async function saveSettings() {
		saving = true;
		error = null;
		try {
			await engineApi.updateSettings({ n8n: { base_url: baseUrl, webhooks } } as any);
		} catch {
			error = 'Failed to save settings';
		} finally {
			saving = false;
		}
	}

	async function testConnection(webhookPath?: string) {
		testing = true;
		testResult = null;
		try {
			testResult = await engineApi.testN8nConnection(baseUrl, webhookPath);
		} catch {
			testResult = { base_url: baseUrl, health: 'error', webhook: null };
		} finally {
			testing = false;
		}
	}

	async function addWebhook() {
		if (!newPlatform.trim() || !newPath.trim()) return;
		webhooks[newPlatform.trim().toLowerCase()] = newPath.trim();
		webhooks = { ...webhooks };
		newPlatform = '';
		newPath = '';
		showAddWebhook = false;
		await saveSettings();
	}

	async function removeWebhook(platform: string) {
		delete webhooks[platform];
		webhooks = { ...webhooks };
		await saveSettings();
	}

	function startEdit(platform: string) {
		editingPlatform = platform;
		editPath = webhooks[platform];
	}

	async function saveEdit() {
		if (editingPlatform && editPath.trim()) {
			webhooks[editingPlatform] = editPath.trim();
			webhooks = { ...webhooks };
			editingPlatform = null;
			editPath = '';
			await saveSettings();
		}
	}

	function cancelEdit() {
		editingPlatform = null;
		editPath = '';
	}

	// Workflow activation toggle
	let togglingWorkflow = $state<string | null>(null);
	let workflowIssues = $state<{ workflowId: string; name: string; issues: string[] } | null>(null);

	async function toggleWorkflowActive(wf: AvailableWorkflow) {
		togglingWorkflow = wf.workflow_id;
		workflowIssues = null;
		workflowError = null;
		try {
			const res = await engineApi.setWorkflowActive(wf.workflow_id, !wf.active);
			// Update local state
			const idx = workflows.findIndex((w) => w.workflow_id === wf.workflow_id);
			if (idx !== -1) {
				workflows[idx] = { ...workflows[idx], active: res.active };
				workflows = [...workflows];
			}
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'Failed to toggle workflow';
			// Try to parse structured issues from the error
			try {
				const parsed = JSON.parse(msg);
				if (parsed.issues && Array.isArray(parsed.issues)) {
					workflowIssues = { workflowId: wf.workflow_id, name: wf.name, issues: parsed.issues };
					return;
				}
			} catch {
				// Not JSON — show as plain error
			}
			workflowError = msg;
		} finally {
			togglingWorkflow = null;
		}
	}

	// Helpers for workflow display
	function getSourceForWorkflow(workflowId: string): Source | undefined {
		return sources.find((s) => s.workflow_id === workflowId);
	}

	const TYPE_LABELS: Record<string, string> = {
		ingestion: 'Ingestion',
		executor: 'Executor'
	};

	// Group workflows: ingestion first, then executor, then unknown
	const groupedWorkflows = $derived.by(() => {
		const ingestion = workflows.filter((w) => w.source_type === 'ingestion');
		const executor = workflows.filter((w) => w.source_type === 'executor');
		const other = workflows.filter((w) => w.source_type !== 'ingestion' && w.source_type !== 'executor');
		const groups: { label: string; items: AvailableWorkflow[] }[] = [];
		if (ingestion.length) groups.push({ label: 'Ingestion Workflows', items: ingestion });
		if (executor.length) groups.push({ label: 'Executor Workflows', items: executor });
		if (other.length) groups.push({ label: 'Other Workflows', items: other });
		return groups;
	});

	const workflowStats = $derived({
		total: workflows.length,
		active: workflows.filter((w) => w.active).length,
		registered: workflows.filter((w) => w.registered).length
	});
</script>

{#if loading}
	<div class="flex items-center justify-center py-12 text-surface-400">
		Loading...
	</div>
{:else}
	<div class="space-y-6">
		{#if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-laya-base text-red-300">
				{error}
			</div>
		{/if}

		<!-- Section 1: n8n Status Bar -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-3">
					<div class="flex items-center gap-2">
						<span
							class="h-2.5 w-2.5 rounded-full {n8nHealth === 'healthy'
								? 'bg-green-500'
								: n8nHealth === 'unreachable'
									? 'bg-red-500'
									: 'bg-yellow-500'}"
						></span>
						<span class="font-medium">n8n</span>
					</div>
					<span class="text-laya-base text-surface-400">
						{#if hasN8nKey && n8nHealth === 'healthy'}
							Connected and ready
						{:else if n8nHealth === 'healthy' && !hasN8nKey}
							Running — needs configuration
						{:else if n8nHealth === 'unreachable'}
							Not running
						{:else}
							Checking...
						{/if}
					</span>
				</div>

				<div class="flex items-center gap-2">
					{#if !hasN8nKey}
						<button
							onclick={doBootstrap}
							disabled={bootstrapping || n8nHealth === 'unreachable'}
							class="rounded-md bg-primary-600 px-4 py-1.5 text-laya-base font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
						>
							{bootstrapping ? 'Configuring...' : 'Auto-configure'}
						</button>
						<button
							onclick={() => (showManualKeyInput = !showManualKeyInput)}
							class="rounded-md px-3 py-1.5 text-laya-base text-surface-400 transition-colors hover:text-surface-200"
						>
							Manual
						</button>
					{:else}
						<button
							onclick={() => (showAdvanced = !showAdvanced)}
							class="rounded-md px-3 py-1.5 text-laya-base text-surface-400 transition-colors hover:text-surface-200"
						>
							{showAdvanced ? 'Hide' : 'Settings'}
						</button>
					{/if}
				</div>
			</div>

			{#if bootstrapResult}
				<div class="mt-3 flex items-center gap-2 text-laya-base">
					<span
						class="h-2 w-2 rounded-full {bootstrapResult.status === 'ready' ||
						bootstrapResult.status === 'already_configured'
							? 'bg-green-500'
							: 'bg-red-500'}"
					></span>
					<span
						class={bootstrapResult.status === 'ready' ||
						bootstrapResult.status === 'already_configured'
							? 'text-green-400'
							: 'text-red-400'}
					>
						{bootstrapResult.message}
					</span>
				</div>
			{/if}

			{#if showManualKeyInput && !hasN8nKey}
				<div class="mt-4 flex gap-3">
					<input
						type="password"
						bind:value={n8nApiKey}
						placeholder="Enter n8n API key"
						class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
					/>
					<button
						onclick={saveN8nApiKey}
						disabled={savingApiKey || !n8nApiKey.trim()}
						class="rounded-md bg-primary-600 px-4 py-2 text-laya-base font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
					>
						{savingApiKey ? 'Saving...' : 'Save Key'}
					</button>
				</div>
			{/if}

			{#if showAdvanced && hasN8nKey}
				<div class="mt-4 space-y-3 border-t border-surface-700 pt-4">
					<div class="flex gap-3">
						<input
							type="url"
							bind:value={baseUrl}
							onblur={saveSettings}
							placeholder="http://localhost:45678"
							class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
						/>
						<button
							onclick={() => checkHealth()}
							class="rounded-md bg-surface-600 px-3 py-2 text-laya-base font-medium transition-colors hover:bg-surface-500"
						>
							Test
						</button>
					</div>
					<div class="flex items-center gap-3">
						<span class="text-laya-secondary text-surface-500">API Key configured</span>
						<button
							onclick={removeN8nApiKey}
							class="text-laya-secondary text-red-400 hover:text-red-300"
						>
							Remove API Key
						</button>
					</div>
					<a
						href={baseUrl}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1.5 rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary font-medium text-surface-300 transition-colors hover:border-laya-orange/40 hover:text-laya-orange"
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
						</svg>
						Open n8n Dashboard
					</a>
				</div>
			{/if}
		</div>

		<!-- Section 2: n8n Workflows -->
		{#if hasN8nKey}
			<!-- Workflow notification toast — positioned over content, no layout shift -->
			{#if workflowError || workflowIssues}
				<div class="pointer-events-none relative h-0">
					<div class="pointer-events-auto absolute left-0 right-0 top-0 z-10">
						{#if workflowError}
							<div class="flex items-center gap-2 rounded-md border border-red-800/50 bg-surface-800 px-3 py-1.5 shadow-lg">
								<span class="text-laya-secondary text-red-300 flex-1 truncate">{workflowError}</span>
								<button onclick={() => (workflowError = null)} aria-label="Dismiss error" class="shrink-0 text-red-500/60 hover:text-red-300 transition-colors">
									<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
								</button>
							</div>
						{:else if workflowIssues}
							<div class="flex items-center gap-2 rounded-md border border-yellow-700/40 bg-surface-800 px-3 py-1.5 shadow-lg">
								<span class="text-laya-secondary text-yellow-300/90 flex-1 truncate">
									<span class="font-medium">Cannot activate "{workflowIssues.name}"</span>
									<span class="text-yellow-400/60"> — </span>
									<span class="text-yellow-300/70">{workflowIssues.issues.join('; ')}</span>
								</span>
								<button onclick={() => (workflowIssues = null)} aria-label="Dismiss warning" class="shrink-0 text-yellow-500/60 hover:text-yellow-300 transition-colors">
									<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
								</button>
							</div>
						{/if}
					</div>
				</div>
			{/if}

			{#if loadingWorkflows}
				<div class="py-8 text-center text-laya-base text-surface-400">Loading workflows...</div>
			{:else if workflows.length === 0}
				<div class="rounded-lg border border-dashed border-surface-700 px-6 py-8 text-center">
					<p class="text-laya-base text-surface-400">No workflows found in n8n</p>
					<p class="mt-1 text-laya-secondary text-surface-500">Create workflows in the n8n dashboard to see them here</p>
				</div>
			{:else}
				<!-- Stats bar -->
				<div class="flex items-center gap-4 text-laya-secondary text-surface-400">
					<span>{workflowStats.total} workflows</span>
					<span class="text-surface-600">|</span>
					<span class="flex items-center gap-1">
						<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
						{workflowStats.active} active
					</span>
					<span class="text-surface-600">|</span>
					<span>{workflowStats.registered} registered as sources</span>
					<button
						onclick={loadWorkflows}
						class="ml-auto text-surface-500 transition-colors hover:text-surface-200"
						title="Refresh"
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h5" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M20 20v-5h-5" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M20.49 9A9 9 0 0 0 5.64 5.64L4 4" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M3.51 15A9 9 0 0 0 18.36 18.36L20 20" />
						</svg>
					</button>
				</div>

				{#each groupedWorkflows as group}
					<div>
						<h3 class="mb-3 text-laya-secondary font-semibold uppercase tracking-wider text-surface-400">
							{group.label}
						</h3>
						<div class="overflow-hidden {$glassTheme ? 'rounded-xl border border-white/[0.06]' : 'rounded-xl border border-surface-700'}">
							<table class="w-full text-laya-base">
								<thead class="{$glassTheme ? 'bg-white/[0.03]' : 'bg-surface-900'} text-left text-laya-secondary uppercase tracking-wider text-surface-500">
									<tr>
										<th class="px-4 py-2.5">Workflow</th>
										<th class="px-4 py-2.5">Platform</th>
										<th class="px-4 py-2.5">Status</th>
										<th class="px-4 py-2.5">Source</th>
									</tr>
								</thead>
								<tbody class="divide-y {$glassTheme ? 'divide-white/[0.06]' : 'divide-surface-700/50'}">
									{#each group.items as wf}
										{@const source = getSourceForWorkflow(wf.workflow_id)}
										<tr class="{$glassTheme ? 'bg-white/[0.03] hover:bg-white/[0.07]' : 'bg-surface-800 hover:bg-surface-700/50'} transition-colors">
											<td class="px-4 py-3">
												<div class="flex items-center gap-2.5">
													<div class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md {wf.active ? 'bg-green-900/30 text-green-400' : ($glassTheme ? 'bg-white/[0.06] text-surface-500' : 'bg-surface-700 text-surface-500')}">
														<PlatformIcon platform={wf.platform} size={16} />
													</div>
													<div class="min-w-0">
														<div class="truncate font-medium text-surface-100" title={wf.name}>{wf.name}</div>
														<div class="truncate text-laya-micro text-surface-500 font-mono" title={wf.workflow_id}>{wf.workflow_id}</div>
													</div>
												</div>
											</td>
											<td class="px-4 py-3">
												<span class="text-laya-secondary font-medium text-surface-300">{wf.platform}</span>
											</td>
											<td class="px-4 py-3">
												<button
													onclick={() => toggleWorkflowActive(wf)}
													disabled={togglingWorkflow === wf.workflow_id}
													class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-laya-micro font-medium transition-colors
														{wf.active
															? 'bg-green-900/30 text-green-400 hover:bg-green-900/50'
															: ($glassTheme ? 'bg-white/[0.08] text-surface-400 hover:bg-white/[0.14]' : 'bg-surface-700 text-surface-400 hover:bg-surface-600')}
														{togglingWorkflow === wf.workflow_id ? 'opacity-50' : ''}"
													title={wf.active ? 'Click to deactivate' : 'Click to activate'}
												>
													<span class="h-1.5 w-1.5 rounded-full {wf.active ? 'bg-green-400' : 'bg-surface-500'}"></span>
													{#if togglingWorkflow === wf.workflow_id}
														...
													{:else}
														{wf.active ? 'Active' : 'Inactive'}
													{/if}
												</button>
											</td>
											<td class="px-4 py-3">
												{#if source}
													<div class="flex items-center gap-1.5">
														{#if source.space_name}
															<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {source.space_name === 'Default' ? '#F97316' : '#6366f1'}"></span>
														{/if}
														<span class="text-laya-secondary text-surface-300" title="Registered as source: {source.name} in {source.space_name ?? 'Default'}">
															{source.space_name ?? 'Default'}
														</span>
													</div>
												{:else}
													<span class="text-laya-secondary text-surface-500">—</span>
												{/if}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>
				{/each}
			{/if}
		{/if}

		<!-- Section 3: Webhook Mappings (collapsed) -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'}">
			<button
				onclick={() => (showWebhooks = !showWebhooks)}
				class="flex w-full items-center justify-between p-4 text-left transition-colors {$glassTheme ? 'hover:bg-white/[0.04]' : 'hover:bg-surface-700/50'}"
			>
				<div>
					<span class="text-laya-base font-medium">Advanced: Webhook Mappings</span>
					<span class="ml-2 text-laya-secondary text-surface-500"
						>({Object.keys(webhooks).length} configured)</span
					>
				</div>
				<svg
					class="h-4 w-4 text-surface-400 transition-transform {showWebhooks
						? 'rotate-180'
						: ''}"
					fill="none"
					viewBox="0 0 24 24"
					stroke="currentColor"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M19 9l-7 7-7-7"
					/>
				</svg>
			</button>

			{#if showWebhooks}
				<div class="border-t border-surface-700 p-5">
					<p class="mb-4 text-laya-base text-surface-400">
						Map each platform to its n8n webhook path. The full URL will be
						<code class="rounded bg-surface-700 px-1 py-0.5 text-laya-secondary"
							>{baseUrl}/webhook/&lt;path&gt;</code
						>
					</p>

					<div class="overflow-hidden {$glassTheme ? 'rounded-xl border border-white/[0.06]' : 'rounded-xl border border-surface-700'}">
						<table class="w-full text-laya-base">
							<thead
								class="{$glassTheme ? 'bg-white/[0.03]' : 'bg-surface-900'} text-left text-laya-secondary uppercase tracking-wider text-surface-400"
							>
								<tr>
									<th class="px-4 py-3">Platform</th>
									<th class="px-4 py-3">Webhook Path</th>
									<th class="px-4 py-3 text-right">Actions</th>
								</tr>
							</thead>
							<tbody class="divide-y {$glassTheme ? 'divide-white/[0.06]' : 'divide-surface-700'}">
								{#each Object.entries(webhooks) as [platform, path]}
									<tr class="{$glassTheme ? 'bg-white/[0.03] hover:bg-white/[0.07]' : 'bg-surface-900 hover:bg-surface-800'} transition-colors">
										<td class="px-4 py-3 font-medium">{platform}</td>
										<td class="px-4 py-3">
											{#if editingPlatform === platform}
												<input
													type="text"
													bind:value={editPath}
													class="w-full rounded border border-surface-600 bg-surface-700 px-2 py-1 text-laya-base"
												/>
											{:else}
												<code class="text-surface-300">{path}</code>
											{/if}
										</td>
										<td class="px-4 py-3 text-right">
											{#if editingPlatform === platform}
												<button
													class="text-green-400 hover:text-green-300"
													onclick={saveEdit}>Save</button
												>
												<button
													class="ml-2 text-surface-400 hover:text-surface-200"
													onclick={cancelEdit}>Cancel</button
												>
											{:else}
												<button
													class="text-surface-400 hover:text-surface-100"
													onclick={() => testConnection(path)}>Test</button
												>
												<button
													class="ml-2 text-surface-400 hover:text-surface-100"
													onclick={() => startEdit(platform)}>Edit</button
												>
												<button
													class="ml-2 text-red-400 hover:text-red-300"
													onclick={() => removeWebhook(platform)}>Remove</button
												>
											{/if}
										</td>
									</tr>
								{/each}
								{#if Object.keys(webhooks).length === 0}
									<tr>
										<td colspan="3" class="px-4 py-6 text-center text-surface-500"
											>No webhook mappings configured</td
										>
									</tr>
								{/if}
							</tbody>
						</table>
					</div>

					{#if testResult?.webhook}
						<div class="mt-3 flex items-center gap-2 text-laya-base">
							<span
								class="h-2 w-2 rounded-full {testResult.webhook.reachable
									? 'bg-green-500'
									: 'bg-yellow-500'}"
							></span>
							<span
								class={testResult.webhook.reachable
									? 'text-green-400'
									: 'text-yellow-400'}
							>
								Webhook "{testResult.webhook.path}": {testResult.webhook.reachable
									? `reachable (${testResult.webhook.status_code})`
									: testResult.webhook.error || 'unreachable'}
							</span>
						</div>
					{/if}

					{#if showAddWebhook}
						<div class="mt-4 flex gap-3">
							<input
								type="text"
								bind:value={newPlatform}
								placeholder="Platform (e.g. github)"
								class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
							/>
							<input
								type="text"
								bind:value={newPath}
								placeholder="Webhook path"
								class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500"
							/>
							<button
								onclick={addWebhook}
								disabled={!newPlatform.trim() || !newPath.trim()}
								class="rounded-md bg-surface-600 px-4 py-2 text-laya-base font-medium hover:bg-surface-500 disabled:opacity-50"
							>
								Add
							</button>
							<button
								onclick={() => (showAddWebhook = false)}
								class="text-laya-base text-surface-400 hover:text-surface-200"
							>
								Cancel
							</button>
						</div>
					{:else}
						<button
							class="mt-4 rounded-lg border border-dashed border-surface-600 px-4 py-2 text-laya-base text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
							onclick={() => (showAddWebhook = true)}
						>
							+ Add Webhook
						</button>
					{/if}

					{#if saving}
						<p class="mt-2 text-laya-secondary text-surface-400">Saving...</p>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}
