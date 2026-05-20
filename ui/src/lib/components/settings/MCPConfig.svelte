<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import type { McpConfig, McpAuthMode, McpToolScopes } from '$lib/api/types';

	let config = $state<McpConfig | null>(null);
	let loading = $state(true);
	let saveError = $state<string | null>(null);
	let revealedToken = $state<string | null>(null);
	let revealing = $state(false);
	let rotating = $state(false);
	let copiedToken = $state(false);
	let copiedConfig = $state(false);
	let showRotateConfirm = $state(false);

	const sectionClass = $derived(
		$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'
	);

	const exampleConfig = $derived(
		config
			? JSON.stringify(
					{
						mcpServers: {
							laya: {
								type: 'sse',
								url: config.sse_url,
								...(config.auth_mode === 'bearer'
									? {
											headers: {
												Authorization: `Bearer ${revealedToken ?? (config.token_prefix ? config.token_prefix + '…' : '<your-token>')}`
											}
									  }
									: {})
							}
						}
					},
					null,
					2
			  )
			: ''
	);

	const readWarning = $derived(config && !config.tool_scopes.read);

	onMount(async () => {
		await refresh();
	});

	async function refresh() {
		loading = true;
		try {
			config = await engineApi.getMcpConfig();
		} catch (e) {
			saveError = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function updateScopes(next: McpToolScopes) {
		if (!config) return;
		saveError = null;
		try {
			config = await engineApi.updateMcpConfig({ tool_scopes: next });
		} catch (e) {
			saveError = e instanceof Error ? e.message : String(e);
		}
	}

	async function toggleScope(scope: keyof McpToolScopes) {
		if (!config) return;
		await updateScopes({ ...config.tool_scopes, [scope]: !config.tool_scopes[scope] });
	}

	async function setAuthMode(mode: McpAuthMode) {
		if (!config || config.auth_mode === mode) return;
		saveError = null;
		try {
			config = await engineApi.updateMcpConfig({ auth_mode: mode });
			revealedToken = null;
		} catch (e) {
			saveError = e instanceof Error ? e.message : String(e);
		}
	}

	async function reveal() {
		if (revealedToken) {
			revealedToken = null;
			return;
		}
		revealing = true;
		try {
			const r = await engineApi.revealMcpToken();
			revealedToken = r.token;
		} catch (e) {
			saveError = e instanceof Error ? e.message : String(e);
		} finally {
			revealing = false;
		}
	}

	async function rotate() {
		showRotateConfirm = false;
		rotating = true;
		try {
			const r = await engineApi.refreshMcpToken();
			revealedToken = r.token;
			await refresh();
		} catch (e) {
			saveError = e instanceof Error ? e.message : String(e);
		} finally {
			rotating = false;
		}
	}

	async function copyToken() {
		if (!revealedToken) return;
		await navigator.clipboard.writeText(revealedToken);
		copiedToken = true;
		setTimeout(() => (copiedToken = false), 1500);
	}

	async function copyExampleConfig() {
		await navigator.clipboard.writeText(exampleConfig);
		copiedConfig = true;
		setTimeout(() => (copiedConfig = false), 1500);
	}
</script>

<div class="space-y-8">
	{#if loading}
		<div class="{sectionClass} p-6 text-laya-base text-surface-400">Loading MCP settings…</div>
	{:else if !config}
		<div class="{sectionClass} p-6 text-laya-base text-error-400">
			Could not load MCP settings.
			{#if saveError}<br />{saveError}{/if}
		</div>
	{:else}
		<!-- Overview / connection URL -->
		<div class="{sectionClass} p-6">
			<h3 class="mb-1 text-laya-heading font-semibold text-surface-50">MCP Server</h3>
			<p class="mb-5 text-laya-base text-surface-400">
				Laya exposes its tools over the Model Context Protocol while the engine is running.
				Wire any MCP-compatible client (Claude Desktop, Cursor, VS Code, custom agents) to the
				URL below. In-app coding agents use the same endpoint and respect the same scope and
				auth settings.
			</p>

			<div class="rounded-lg border border-surface-700 bg-surface-900 p-4">
				<div class="mb-2 text-laya-micro uppercase tracking-wider text-surface-400">SSE URL</div>
				<div class="flex items-center gap-3">
					<code class="flex-1 break-all font-mono text-laya-secondary text-laya-peach">{config.sse_url}</code>
					<button
						class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-laya-orange hover:text-laya-orange"
						onclick={() => {
							navigator.clipboard.writeText(config!.sse_url);
						}}
					>
						Copy
					</button>
				</div>
			</div>
		</div>

		<!-- Tool scopes -->
		<div class="{sectionClass} p-6">
			<h3 class="mb-1 text-laya-heading font-semibold text-surface-50">Tool Scopes</h3>
			<p class="mb-5 text-laya-base text-surface-400">
				Pick which tool categories MCP clients are allowed to call. Changes take effect on the
				next call &mdash; no restart needed.
			</p>

			{#each [
				{ key: 'read' as const, label: 'Read', desc: 'Search and fetch cards, events, entities, semantic search, settings introspection.' },
				{ key: 'write' as const, label: 'Write', desc: 'Card lifecycle changes (dismiss, archive, mark done, reopen) and settings updates.' },
				{ key: 'egress' as const, label: 'Egress', desc: 'Send Slack messages, post PR comments, create Jira issues, and other outbound actions across connected platforms.' }
			] as scope}
				<div class="mb-3 flex items-start justify-between gap-4 rounded-lg border border-surface-700 bg-surface-900 p-4 last:mb-0">
					<div class="min-w-0 flex-1">
						<div class="text-laya-base font-medium text-surface-100">{scope.label}</div>
						<div class="mt-1 text-laya-secondary text-surface-400">{scope.desc}</div>
					</div>
					<button
						class="relative mt-1 h-6 w-11 shrink-0 rounded-full transition-colors {config.tool_scopes[scope.key] ? 'bg-laya-orange' : 'bg-surface-600'}"
						onclick={() => toggleScope(scope.key)}
						role="switch"
						aria-checked={config.tool_scopes[scope.key]}
						aria-label="{scope.label} scope"
					>
						<span
							class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {config.tool_scopes[scope.key] ? 'translate-x-5' : 'translate-x-0'}"
						></span>
					</button>
				</div>
			{/each}

			{#if readWarning}
				<div class="mt-4 rounded-lg border border-amber-700/50 bg-amber-900/20 p-4 text-laya-secondary text-amber-200">
					<strong>Read is off.</strong> In-app coding agents rely on Read to search cards and
					fetch context. With Read disabled, agents will run with no Laya context.
				</div>
			{/if}
		</div>

		<!-- Authentication -->
		<div class="{sectionClass} p-6">
			<h3 class="mb-1 text-laya-heading font-semibold text-surface-50">Authentication</h3>
			<p class="mb-5 text-laya-base text-surface-400">
				Bearer tokens are the safe default. Use loopback-only if you trust every process on this
				machine and want zero-friction setup.
			</p>

			<div class="space-y-3">
				<button
					class="w-full rounded-lg border-2 p-4 text-left transition-colors {config.auth_mode === 'bearer' ? 'border-laya-orange bg-surface-700' : 'border-surface-600 bg-surface-900 hover:border-surface-500'}"
					onclick={() => setAuthMode('bearer')}
				>
					<div class="text-laya-base font-medium text-surface-100">Bearer token</div>
					<div class="mt-1 text-laya-secondary text-surface-400">
						Clients must include <code class="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-laya-micro text-laya-peach">Authorization: Bearer &lt;token&gt;</code>
						in every request. Recommended.
					</div>
				</button>

				<button
					class="w-full rounded-lg border-2 p-4 text-left transition-colors {config.auth_mode === 'none' ? 'border-laya-orange bg-surface-700' : 'border-surface-600 bg-surface-900 hover:border-surface-500'}"
					onclick={() => setAuthMode('none')}
				>
					<div class="text-laya-base font-medium text-surface-100">Loopback only, no auth</div>
					<div class="mt-1 text-laya-secondary text-surface-400">
						Anything on this machine that can reach <code class="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-laya-micro text-laya-peach">127.0.0.1:8420</code>
						can call MCP. No header required.
					</div>
				</button>
			</div>

			{#if config.auth_mode === 'bearer'}
				<div class="mt-5 rounded-lg border border-surface-700 bg-surface-900 p-4">
					<div class="mb-2 flex items-center justify-between gap-3">
						<span class="text-laya-micro uppercase tracking-wider text-surface-400">Token</span>
						{#if config.has_token && config.token_prefix}
							<span class="font-mono text-laya-micro text-surface-500">{config.token_prefix}…</span>
						{/if}
					</div>

					{#if revealedToken}
						<div class="flex items-center gap-3">
							<code class="flex-1 break-all font-mono text-laya-secondary text-laya-peach">{revealedToken}</code>
							<button
								class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-laya-orange hover:text-laya-orange"
								onclick={copyToken}
							>
								{copiedToken ? 'Copied' : 'Copy'}
							</button>
							<button
								class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-surface-500"
								onclick={() => (revealedToken = null)}
							>
								Hide
							</button>
						</div>
					{:else}
						<div class="flex items-center gap-3">
							<input
								type="password"
								value={config.has_token ? '••••••••••••••••••••••••••••••••' : ''}
								readonly
								class="flex-1 rounded-md border border-surface-700 bg-surface-800 px-3 py-1.5 font-mono text-laya-secondary text-surface-300"
							/>
							<button
								class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-laya-orange hover:text-laya-orange disabled:opacity-50"
								onclick={reveal}
								disabled={!config.has_token || revealing}
							>
								{revealing ? '…' : 'Show'}
							</button>
						</div>
					{/if}

					<div class="mt-4 flex items-center justify-between gap-3">
						<div class="text-laya-secondary text-surface-400">
							Rotating the token immediately invalidates the old one. Any client using the
							old token will be disconnected on its next request.
						</div>
						{#if showRotateConfirm}
							<div class="flex shrink-0 gap-2">
								<button
									class="rounded-md border border-amber-700 bg-amber-900/40 px-3 py-1.5 text-laya-secondary font-medium text-amber-200 transition-colors hover:bg-amber-900/60"
									onclick={rotate}
									disabled={rotating}
								>
									{rotating ? 'Rotating…' : 'Confirm rotate'}
								</button>
								<button
									class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-surface-500"
									onclick={() => (showRotateConfirm = false)}
								>
									Cancel
								</button>
							</div>
						{:else}
							<button
								class="shrink-0 rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-laya-orange hover:text-laya-orange"
								onclick={() => (showRotateConfirm = true)}
							>
								Rotate token
							</button>
						{/if}
					</div>
				</div>
			{/if}
		</div>

		<!-- Example client config -->
		<div class="{sectionClass} p-6">
			<h3 class="mb-1 text-laya-heading font-semibold text-surface-50">Client Configuration</h3>
			<p class="mb-5 text-laya-base text-surface-400">
				Paste this into any MCP-compatible client &mdash; Claude Desktop, Cursor, VS Code, Codex CLI, Gemini CLI,
				Pi, or custom agent harnesses. For Claude Desktop on macOS the config file is at
				<code class="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-laya-micro text-laya-peach">~/Library/Application Support/Claude/claude_desktop_config.json</code>.
			</p>

			<div class="rounded-lg border border-surface-700 bg-surface-900 p-4">
				<div class="mb-3 flex items-center justify-between">
					<span class="text-laya-micro uppercase tracking-wider text-surface-400">JSON</span>
					<button
						class="rounded-md border border-surface-600 px-3 py-1.5 text-laya-secondary text-surface-200 transition-colors hover:border-laya-orange hover:text-laya-orange"
						onclick={copyExampleConfig}
					>
						{copiedConfig ? 'Copied' : 'Copy'}
					</button>
				</div>
				<pre class="overflow-x-auto font-mono text-laya-secondary text-surface-200"><code>{exampleConfig}</code></pre>
			</div>

			<p class="mt-4 text-laya-secondary text-surface-400">
				To scope a client to one Laya space, append <code class="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-laya-micro text-laya-peach">?space_id=&lt;your-space&gt;</code>
				to the URL. Register the same server multiple times under different names with
				different space IDs to give each client its own scoped view.
			</p>
		</div>

		{#if saveError}
			<div class="rounded-lg border border-error-700 bg-error-900/30 p-4 text-laya-secondary text-error-200">
				{saveError}
			</div>
		{/if}
	{/if}
</div>
