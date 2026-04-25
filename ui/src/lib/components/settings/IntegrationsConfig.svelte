<script lang="ts">
	import { untrack } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import PlatformCard from './PlatformCard.svelte';
	import ConnectModal from './ConnectModal.svelte';
	import N8nAdvancedSection from './N8nAdvancedSection.svelte';
	import type { PlatformConfig, EgressConnection, FieldDef } from '$lib/api/types';

	// Platform registry + connections
	let platforms = $state<Record<string, PlatformConfig>>({});
	let connections = $state<EgressConnection[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Connect modal state
	let connectingPlatform = $state<string | null>(null);
	let connectingLabel = $state('');
	let connectingIsOAuth = $state(false);
	let connectingFields = $state<FieldDef[]>([]);

	// n8n advanced section
	let showN8nAdvanced = $state(false);

	// Category config
	const CATEGORY_ORDER = ['development', 'project_management', 'communication', 'google', 'microsoft', 'email'];
	const CATEGORY_LABELS: Record<string, string> = {
		development: 'Development',
		project_management: 'Project Management',
		communication: 'Communication',
		google: 'Google',
		microsoft: 'Microsoft',
		email: 'Email'
	};

	// Load data on mount (once). loadData() reads `platforms` to decide
	// whether to show the loading skeleton, which Svelte 5 would track as
	// a dependency — then setting `platforms` after the fetch re-triggers
	// the effect, creating an infinite request loop. Using untrack prevents this.
	$effect(() => {
		untrack(() => loadData());
	});

	async function loadData() {
		// Only show the full loading skeleton on initial load; refreshes
		// (e.g. after disconnect) update in-place to preserve scroll position.
		const isInitial = platforms && Object.keys(platforms).length === 0;
		if (isInitial) loading = true;
		error = null;
		try {
			const [platformsResp, connectionsResp] = await Promise.all([
				engineApi.getPlatforms(),
				engineApi.listEgressConnections()
			]);
			platforms = platformsResp.platforms;
			connections = connectionsResp.connections;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load integrations';
		} finally {
			loading = false;
		}
	}

	// Merge platforms with connection status, grouped by category
	interface PlatformWithConnections {
		key: string;
		config: PlatformConfig;
		connections: EgressConnection[];
	}

	const categorizedPlatforms = $derived.by(() => {
		const groups: { category: string; label: string; items: PlatformWithConnections[] }[] = [];

		for (const cat of CATEGORY_ORDER) {
			const items: PlatformWithConnections[] = [];

			if (cat === 'email') {
				// Add SMTP as a virtual platform
				const smtpConns = connections.filter((c) => c.platform === 'smtp');
				items.push({
					key: 'smtp',
					config: {
						label: 'Email (SMTP)',
						category: 'email',
						icon: 'smtp',
						n8n_type: '',
						n8n_node: '',
						oauth: false,
						fields: []
					},
					connections: smtpConns
				});
			} else {
				for (const [key, config] of Object.entries(platforms)) {
					if (config.category === cat) {
						const conns = connections.filter((c) => c.platform === key);
						items.push({ key, config, connections: conns });
					}
				}
			}

			if (items.length > 0) {
				groups.push({ category: cat, label: CATEGORY_LABELS[cat] || cat, items });
			}
		}

		return groups;
	});

	const connectionStats = $derived({
		total: Object.keys(platforms).length + 1, // +1 for SMTP
		connected: connections.filter((c) => c.status === 'connected').length
	});

	function openConnectModal(platformKey: string) {
		if (platformKey === 'smtp') {
			connectingPlatform = 'smtp';
			connectingLabel = 'Email (SMTP)';
			connectingIsOAuth = false;
			connectingFields = [];
			return;
		}

		const config = platforms[platformKey];
		if (!config) return;

		connectingPlatform = platformKey;
		connectingLabel = config.label;
		connectingIsOAuth = config.oauth;
		connectingFields = config.fields;
	}

	function closeConnectModal() {
		connectingPlatform = null;
	}

	function handleConnected() {
		closeConnectModal();
		loadData(); // Refresh connections
	}
</script>

{#if loading}
	<div class="flex items-center justify-center py-12 text-surface-400">
		Loading integrations...
	</div>
{:else}
	<div class="space-y-8">
		{#if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">
				{error}
			</div>
		{/if}

		<!-- Stats -->
		<div class="flex items-center gap-3 text-xs text-surface-400">
			<span class="flex items-center gap-1.5">
				<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
				{connectionStats.connected} connected
			</span>
			<span class="text-surface-600">·</span>
			<span>{connectionStats.total} platforms available</span>
		</div>

		<!-- Platform grid by category -->
		{#each categorizedPlatforms as group}
			<div>
				<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-surface-500">
					{group.label}
				</h3>
				<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
					{#each group.items as p}
						<PlatformCard
							platformKey={p.key}
							label={p.config.label}
							category={p.config.category}
							isOAuth={p.config.oauth}
							connections={p.connections}
							onConnect={openConnectModal}
							onRefresh={loadData}
						/>
					{/each}
				</div>
			</div>
		{/each}

		<!-- n8n Advanced Section -->
		<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'}">
			<button
				onclick={() => (showN8nAdvanced = !showN8nAdvanced)}
				class="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-surface-700/50"
			>
				<div>
					<span class="text-sm font-medium text-surface-300">Advanced: n8n Workflow Engine</span>
					<span class="ml-2 text-xs text-surface-500">Manage workflows, webhooks, and n8n configuration</span>
				</div>
				<svg
					class="h-4 w-4 text-surface-400 transition-transform {showN8nAdvanced ? 'rotate-180' : ''}"
					fill="none"
					viewBox="0 0 24 24"
					stroke="currentColor"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
				</svg>
			</button>

			{#if showN8nAdvanced}
				<div class="border-t border-surface-700 p-5">
					<N8nAdvancedSection />
				</div>
			{/if}
		</div>
	</div>
{/if}

<!-- Connect Modal -->
{#if connectingPlatform}
	<ConnectModal
		platform={connectingPlatform}
		platformLabel={connectingLabel}
		isOAuth={connectingIsOAuth}
		fields={connectingFields}
		hasExistingConnections={connections.some(c => c.platform === connectingPlatform)}
		onClose={closeConnectModal}
		onConnected={handleConnected}
	/>
{/if}
