<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import PlatformIcon from './PlatformIcon.svelte';
	import type {
		N8nTestResult,
		N8nConnection,
		PlatformConfig,
		ConnectionTestResult,
		N8nBootstrapResponse
	} from '$lib/api/types';

	const DEFAULT_WEBHOOKS: Record<string, string> = {
		jira: 'jira-executor',
		bitbucket: 'bitbucket-executor',
		slack: 'slack-executor',
		gmail: 'gmail-executor',
		calendar: 'calendar-executor'
	};

	const CATEGORY_LABELS: Record<string, string> = {
		development: 'Development',
		project_management: 'Project Management',
		communication: 'Communication',
		google: 'Google'
	};

	const CATEGORY_ORDER = ['development', 'project_management', 'communication', 'google'];

	// Loading / error
	let loading = $state(true);
	let error = $state<string | null>(null);

	// n8n connection
	let baseUrl = $state('http://localhost:5678');
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

	// Platform connections
	let platforms = $state<Record<string, PlatformConfig>>({});
	let connections = $state<N8nConnection[]>([]);
	let loadingConnections = $state(false);
	let connectionError = $state<string | null>(null);

	// Connect form
	let connectingPlatform = $state<string | null>(null);
	let connectionName = $state('');
	let credentialFields = $state<Record<string, string>>({});
	let creatingConnection = $state(false);

	// Manage panel
	let managingPlatform = $state<string | null>(null);
	let deletingConnectionId = $state<string | null>(null);

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
				await loadConnections();
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

	async function loadConnections() {
		loadingConnections = true;
		connectionError = null;
		try {
			const [platformsResp, connectionsResp] = await Promise.all([
				engineApi.getPlatforms(),
				engineApi.getConnections()
			]);
			platforms = platformsResp.platforms;
			connections = connectionsResp.connections;
		} catch (e) {
			connectionError = e instanceof Error ? e.message : 'Failed to load connections';
		} finally {
			loadingConnections = false;
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
				await loadConnections();
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
			await loadConnections();
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
			connections = [];
			bootstrapResult = null;
		} catch {
			error = 'Failed to remove n8n API key';
		}
	}

	function startConnect(platformKey: string) {
		connectingPlatform = platformKey;
		managingPlatform = null;
		const platform = platforms[platformKey];
		connectionName = platform?.label || '';
		credentialFields = {};
		if (platform) {
			for (const field of platform.fields) {
				credentialFields[field.key] = '';
			}
		}
	}

	function cancelConnect() {
		connectingPlatform = null;
		connectionName = '';
		credentialFields = {};
		connectionError = null;
	}

	async function createConnection() {
		if (!connectingPlatform || !connectionName.trim()) return;
		creatingConnection = true;
		connectionError = null;
		try {
			await engineApi.createConnection({
				platform: connectingPlatform,
				name: connectionName.trim(),
				credentials: { ...credentialFields }
			});
			connectingPlatform = null;
			connectionName = '';
			credentialFields = {};
			await loadConnections();
		} catch (e) {
			connectionError = e instanceof Error ? e.message : 'Failed to create connection';
		} finally {
			creatingConnection = false;
		}
	}

	function startManage(platformKey: string) {
		managingPlatform = managingPlatform === platformKey ? null : platformKey;
		connectingPlatform = null;
	}

	async function deleteConnection(id: string) {
		deletingConnectionId = id;
		connectionError = null;
		try {
			await engineApi.deleteConnection(id);
			connections = connections.filter((c) => c.id !== id);
			managingPlatform = null;
		} catch (e) {
			connectionError = e instanceof Error ? e.message : 'Failed to delete connection';
		} finally {
			deletingConnectionId = null;
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

	// Derived: group platforms by category
	let platformsByCategory = $derived(() => {
		const grouped: Record<string, Array<[string, PlatformConfig]>> = {};
		for (const cat of CATEGORY_ORDER) {
			grouped[cat] = [];
		}
		for (const [key, platform] of Object.entries(platforms)) {
			const cat = platform.category || 'other';
			if (!grouped[cat]) grouped[cat] = [];
			grouped[cat].push([key, platform]);
		}
		return grouped;
	});

	// Derived: find connection for a platform
	function getConnectionForPlatform(platformKey: string): N8nConnection | undefined {
		const platform = platforms[platformKey];
		if (!platform) return undefined;
		return connections.find((c) => c.platform === platformKey);
	}
</script>

{#if loading}
	<div class="flex items-center justify-center py-12 text-surface-400">
		Loading integrations...
	</div>
{:else}
	<div class="space-y-6">
		{#if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">
				{error}
			</div>
		{/if}

		<!-- Section 1: n8n Status Bar -->
		<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
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
					<span class="text-sm text-surface-400">
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
							class="rounded-md bg-primary-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
						>
							{bootstrapping ? 'Configuring...' : 'Auto-configure'}
						</button>
						<button
							onclick={() => (showManualKeyInput = !showManualKeyInput)}
							class="rounded-md px-3 py-1.5 text-sm text-surface-400 transition-colors hover:text-surface-200"
						>
							Manual
						</button>
					{:else}
						<button
							onclick={() => (showAdvanced = !showAdvanced)}
							class="rounded-md px-3 py-1.5 text-sm text-surface-400 transition-colors hover:text-surface-200"
						>
							{showAdvanced ? 'Hide' : 'Settings'}
						</button>
					{/if}
				</div>
			</div>

			{#if bootstrapResult}
				<div class="mt-3 flex items-center gap-2 text-sm">
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
						class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
					/>
					<button
						onclick={saveN8nApiKey}
						disabled={savingApiKey || !n8nApiKey.trim()}
						class="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
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
							placeholder="http://localhost:5678"
							class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
						/>
						<button
							onclick={() => checkHealth()}
							class="rounded-md bg-surface-600 px-3 py-2 text-sm font-medium transition-colors hover:bg-surface-500"
						>
							Test
						</button>
					</div>
					<div class="flex items-center gap-3">
						<span class="text-xs text-surface-500">API Key configured</span>
						<button
							onclick={removeN8nApiKey}
							class="text-xs text-red-400 hover:text-red-300"
						>
							Remove API Key
						</button>
					</div>
				</div>
			{/if}
		</div>

		<!-- Section 2: Platform Grid -->
		{#if hasN8nKey}
			{#if connectionError}
				<div
					class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300"
				>
					{connectionError}
				</div>
			{/if}

			{#if loadingConnections}
				<div class="py-8 text-center text-sm text-surface-400">Loading platforms...</div>
			{:else}
				{#each CATEGORY_ORDER as category}
					{@const categoryPlatforms = platformsByCategory()[category] || []}
					{#if categoryPlatforms.length > 0}
						<div>
							<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-surface-400">
								{CATEGORY_LABELS[category] || category}
							</h3>
							<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
								{#each categoryPlatforms as [key, platform]}
									{@const conn = getConnectionForPlatform(key)}
									{@const isConnected = !!conn}
									{@const isConnecting = connectingPlatform === key}
									{@const isManaging = managingPlatform === key}

									<div
										class="rounded-lg border transition-colors
											{isConnected
											? 'border-green-800/50 bg-surface-800'
											: 'border-surface-700 bg-surface-800'}
											{isConnecting || isManaging ? 'col-span-full' : ''}"
									>
										<!-- Card header -->
										<div class="flex items-center justify-between p-4">
											<div class="flex min-w-0 items-center gap-3">
												<div
													class="flex h-9 w-9 items-center justify-center rounded-lg
														{isConnected ? 'bg-green-900/30 text-green-400' : 'bg-surface-700 text-surface-300'}"
												>
													<PlatformIcon platform={platform.icon || key} size={20} />
												</div>
												<div class="min-w-0">
													<div class="truncate text-sm font-medium">{platform.label}</div>
													{#if isConnected && conn}
														<div class="truncate text-xs text-green-400">{conn.name}</div>
													{:else if platform.oauth}
														<div class="text-xs text-surface-500">OAuth</div>
													{/if}
												</div>
											</div>
											<div class="shrink-0">
												{#if platform.oauth}
													<a
														href="{baseUrl}/credentials/new"
														target="_blank"
														rel="noopener noreferrer"
														class="rounded-md bg-surface-700 px-3 py-1.5 text-xs font-medium text-surface-300 transition-colors hover:bg-surface-600"
													>
														Setup in n8n
													</a>
												{:else if isConnected}
													<button
														onclick={() => startManage(key)}
														class="rounded-md bg-surface-700 px-3 py-1.5 text-xs font-medium text-surface-300 transition-colors hover:bg-surface-600"
													>
														{isManaging ? 'Close' : 'Manage'}
													</button>
												{:else}
													<button
														onclick={() => startConnect(key)}
														class="rounded-md bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500"
													>
														Connect
													</button>
												{/if}
											</div>
										</div>

										<!-- Connect form (expanded) -->
										{#if isConnecting && !platform.oauth}
											<div
												class="border-t border-surface-700 bg-surface-900/50 p-4"
											>
												<div class="space-y-3">
													<div>
														<label
															for="conn-name-{key}"
															class="mb-1 block text-xs font-medium text-surface-400"
															>Connection Name</label
														>
														<input
															id="conn-name-{key}"
															type="text"
															bind:value={connectionName}
															placeholder="e.g. My {platform.label}"
															class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
														/>
													</div>

													{#each platform.fields as field}
														<div>
															<label
																for="field-{key}-{field.key}"
																class="mb-1 block text-xs font-medium text-surface-400"
																>{field.label}</label
															>
															<input
																id="field-{key}-{field.key}"
																type={field.type}
																bind:value={credentialFields[field.key]}
																placeholder={field.placeholder ?? ''}
																class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
															/>
															{#if field.help}
																<p class="mt-1 text-xs text-surface-500">
																	{field.help}
																</p>
															{/if}
														</div>
													{/each}

													<div class="flex gap-3 pt-1">
														<button
															onclick={createConnection}
															disabled={creatingConnection || !connectionName.trim()}
															class="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
														>
															{creatingConnection
																? 'Connecting...'
																: 'Save Connection'}
														</button>
														<button
															onclick={cancelConnect}
															class="text-sm text-surface-400 hover:text-surface-200"
														>
															Cancel
														</button>
													</div>
												</div>
											</div>
										{/if}

										<!-- Manage panel (expanded) -->
										{#if isManaging && conn}
											<div
												class="border-t border-surface-700 bg-surface-900/50 p-4"
											>
												<div class="space-y-3">
													<div class="flex items-center justify-between text-sm">
														<span class="text-surface-400">Connection</span>
														<span class="text-surface-200">{conn.name}</span>
													</div>
													<div class="flex items-center justify-between text-sm">
														<span class="text-surface-400">Created</span>
														<span class="text-surface-200">
															{conn.created_at
																? new Date(
																		conn.created_at
																	).toLocaleDateString()
																: '—'}
														</span>
													</div>
													<p class="text-xs text-surface-500">
														Credentials are encrypted in n8n. To change them,
														disconnect and reconnect.
													</p>
													<button
														onclick={() => deleteConnection(conn.id)}
														disabled={deletingConnectionId === conn.id}
														class="rounded-md bg-red-900/50 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-900/80 disabled:opacity-50"
													>
														{deletingConnectionId === conn.id
															? 'Disconnecting...'
															: 'Disconnect'}
													</button>
												</div>
											</div>
										{/if}
									</div>
								{/each}
							</div>
						</div>
					{/if}
				{/each}
			{/if}
		{/if}

		<!-- Section 3: Webhook Mappings (collapsed) -->
		<div class="rounded-lg border border-surface-700 bg-surface-800">
			<button
				onclick={() => (showWebhooks = !showWebhooks)}
				class="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-surface-700/50"
			>
				<div>
					<span class="text-sm font-medium">Advanced: Webhook Mappings</span>
					<span class="ml-2 text-xs text-surface-500"
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
					<p class="mb-4 text-sm text-surface-400">
						Map each platform to its n8n webhook path. The full URL will be
						<code class="rounded bg-surface-700 px-1 py-0.5 text-xs"
							>{baseUrl}/webhook/&lt;path&gt;</code
						>
					</p>

					<div class="overflow-hidden rounded-xl border border-surface-700">
						<table class="w-full text-sm">
							<thead
								class="bg-surface-900 text-left text-xs uppercase tracking-wider text-surface-400"
							>
								<tr>
									<th class="px-4 py-3">Platform</th>
									<th class="px-4 py-3">Webhook Path</th>
									<th class="px-4 py-3 text-right">Actions</th>
								</tr>
							</thead>
							<tbody class="divide-y divide-surface-700">
								{#each Object.entries(webhooks) as [platform, path]}
									<tr class="bg-surface-900 hover:bg-surface-800">
										<td class="px-4 py-3 font-medium">{platform}</td>
										<td class="px-4 py-3">
											{#if editingPlatform === platform}
												<input
													type="text"
													bind:value={editPath}
													class="w-full rounded border border-surface-600 bg-surface-700 px-2 py-1 text-sm"
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
						<div class="mt-3 flex items-center gap-2 text-sm">
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
								class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
							/>
							<input
								type="text"
								bind:value={newPath}
								placeholder="Webhook path"
								class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
							/>
							<button
								onclick={addWebhook}
								disabled={!newPlatform.trim() || !newPath.trim()}
								class="rounded-md bg-surface-600 px-4 py-2 text-sm font-medium hover:bg-surface-500 disabled:opacity-50"
							>
								Add
							</button>
							<button
								onclick={() => (showAddWebhook = false)}
								class="text-sm text-surface-400 hover:text-surface-200"
							>
								Cancel
							</button>
						</div>
					{:else}
						<button
							class="mt-4 rounded-lg border border-dashed border-surface-600 px-4 py-2 text-sm text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
							onclick={() => (showAddWebhook = true)}
						>
							+ Add Webhook
						</button>
					{/if}

					{#if saving}
						<p class="mt-2 text-xs text-surface-400">Saving...</p>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}
