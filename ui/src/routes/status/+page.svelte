<script lang="ts">
	import { onMount } from 'svelte';
	import { health, healthError } from '$lib/stores/health';
	import { wsStatus, lastMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import type { DashboardResponse } from '$lib/api/types';
	import StatCard from '$lib/components/dashboard/StatCard.svelte';
	import BarChart from '$lib/components/dashboard/BarChart.svelte';
	import DonutChart from '$lib/components/dashboard/DonutChart.svelte';
	import FeatureCostChart from '$lib/components/dashboard/FeatureCostChart.svelte';

	const cardClass = $derived($glassTheme ? 'rounded-xl glass-section p-4' : 'rounded-xl border border-surface-700 bg-surface-800 p-4');

	// --- Service health ---
	function statusIcon(status: string | undefined): string {
		if (status === 'healthy' || status === 'connected' || status === 'available') return 'text-green-400';
		if (status === 'unreachable' || status === 'not_configured') return 'text-surface-500';
		return 'text-red-400';
	}

	function statusLabel(status: string | undefined, fallback = 'unknown'): string {
		return status ?? fallback;
	}

	// n8n process management (available in Tauri)
	let n8nProcessStatus = $state('checking...');
	let n8nAction = $state('');

	async function invoke(cmd: string): Promise<any> {
		try {
			const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
			return await tauriInvoke(cmd);
		} catch {
			return null;
		}
	}

	async function checkN8nProcess() {
		const status = await invoke('n8n_status');
		n8nProcessStatus = status ?? 'unknown';
	}

	async function startN8n() {
		n8nAction = 'starting';
		await invoke('start_n8n');
		await new Promise((r) => setTimeout(r, 2000));
		await checkN8nProcess();
		n8nAction = '';
	}

	async function stopN8n() {
		n8nAction = 'stopping';
		await invoke('stop_n8n');
		await new Promise((r) => setTimeout(r, 1000));
		await checkN8nProcess();
		n8nAction = '';
	}

	// --- Dashboard / Analytics ---
	let dashboard: DashboardResponse | null = $state(null);
	let dashLoading = $state(true);
	let dashError = $state('');
	let days = $state(30);

	async function loadDashboard() {
		dashLoading = true;
		dashError = '';
		try {
			dashboard = await engineApi.getDashboard(days);
		} catch (e) {
			dashError = e instanceof Error ? e.message : 'Failed to load analytics';
		} finally {
			dashLoading = false;
			// Scroll to hash anchor after data loads (e.g. #cost from footer link)
			if (window.location.hash) {
				requestAnimationFrame(() => {
					const el = document.querySelector(window.location.hash);
					el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
				});
			}
		}
	}

	const cardStatusData = $derived.by(() => {
		const d = dashboard;
		if (!d) return [];
		return [
			{ label: 'Pending', value: d.stats.cards_pending, color: '#facc15' },
			{ label: 'Approved', value: d.stats.cards_approved, color: '#4ade80' },
			{ label: 'Dismissed', value: d.stats.cards_dismissed, color: '#64748b' }
		];
	});

	const actionStatusData = $derived.by(() => {
		const d = dashboard;
		if (!d) return [];
		return [
			{ label: 'Completed', value: d.stats.actions_completed, color: '#4ade80' },
			{ label: 'Failed', value: d.stats.actions_failed, color: '#f87171' },
			{
				label: 'In Progress',
				value: Math.max(0, d.stats.actions_executed - d.stats.actions_completed - d.stats.actions_failed),
				color: '#60a5fa'
			}
		];
	});

	const sourceData = $derived.by(() => {
		const d = dashboard;
		if (!d) return [];
		return d.events_by_source.map((s: { source: string; count: number }) => ({ label: s.source, value: s.count }));
	});

	const approvalData = $derived.by(() => {
		const d = dashboard;
		if (!d) return [];
		return d.approval_by_persona.map((p: { persona: string; rate: number }) => ({
			label: p.persona,
			value: Math.round(p.rate * 100)
		}));
	});

	const costByModel = $derived.by(() => {
		const d = dashboard;
		if (!d) return [];
		return Object.entries(d.llm_costs.by_model).map(([label, value]) => ({
			label,
			value: Math.round((value as number) * 1000) / 1000
		}));
	});


	function formatCost(usd: number): string {
		return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
	}

	function formatTokens(n: number): string {
		if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
		if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
		return String(n);
	}

	function formatTime(minutes: number): string {
		if (minutes >= 60) return `${(minutes / 60).toFixed(1)}h`;
		return `${Math.round(minutes)}m`;
	}

	onMount(() => {
		checkN8nProcess();
		loadDashboard();
	});
</script>

<svelte:head>
	<title>Status - Laya</title>
</svelte:head>

<div class="mx-auto max-w-6xl space-y-8">
	<!-- System Status section -->
	<section>
		<h2 class="mb-4 text-lg font-semibold">System Status</h2>
		<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
			<!-- Engine -->
			<div class={cardClass}>
				<div class="mb-1.5 text-[10px] uppercase tracking-wider text-surface-400">Engine</div>
				{#if $healthError || !$health}
					<span class="text-sm text-red-400">Offline</span>
				{:else}
					<span class="text-sm {statusIcon($health.engine)}">{statusLabel($health.engine)}</span>
					<div class="mt-1 text-[10px] text-surface-500">Uptime: {Math.floor($health.uptime_seconds / 3600)}h {Math.floor(($health.uptime_seconds % 3600) / 60)}m {Math.floor($health.uptime_seconds % 60)}s</div>
				{/if}
			</div>

			<!-- SQLite -->
			<div class={cardClass}>
				<div class="mb-1.5 text-[10px] uppercase tracking-wider text-surface-400">SQLite</div>
				{#if $healthError || !$health}
					<span class="text-sm text-red-400">Offline</span>
				{:else}
					<span class="text-sm {statusIcon($health.sqlite)}">{statusLabel($health.sqlite)}</span>
				{/if}
			</div>

			<!-- n8n -->
			<div class={cardClass}>
				<div class="mb-1.5 text-[10px] uppercase tracking-wider text-surface-400">n8n</div>
				{#if $healthError || !$health}
					<span class="text-sm text-red-400">Offline</span>
				{:else}
					<span class="text-sm {statusIcon($health.n8n)}">{statusLabel($health.n8n)}</span>
				{/if}
			</div>

			<!-- WebSocket -->
			<div class={cardClass}>
				<div class="mb-1.5 text-[10px] uppercase tracking-wider text-surface-400">WebSocket</div>
				<span class="text-sm {statusIcon($wsStatus === 'connected' ? 'healthy' : 'unhealthy')}">
					{$wsStatus}
				</span>
			</div>
		</div>

		<!-- Embeddings info -->
		{#if $health?.embeddings}
			{@const emb = $health.embeddings}
			<div class="mt-3 {cardClass}">
				<div class="flex items-center justify-between">
					<div>
						<div class="text-[10px] uppercase tracking-wider text-surface-400">Embeddings</div>
						<div class="mt-1 flex items-center gap-2">
							<span class="text-sm text-surface-200">{emb.model}</span>
							<span class="text-[10px] text-surface-500">{emb.dimensions}d</span>
							{#if emb.status === 'fallback'}
								<span class="rounded-full bg-laya-gold/20 px-2 py-0.5 text-[10px] font-medium text-laya-amber">Fallback</span>
							{:else if emb.status === 'active'}
								<span class="rounded-full bg-green-500/20 px-2 py-0.5 text-[10px] font-medium text-green-400">Active</span>
							{:else}
								<span class="rounded-full bg-surface-600/50 px-2 py-0.5 text-[10px] font-medium text-surface-400">{emb.status}</span>
							{/if}
						</div>
					</div>
					<div class="text-right">
						<div class="text-[10px] text-surface-500">Backend</div>
						<div class="mt-0.5 text-xs text-surface-300">
							{#if emb.backend === 'nomic'}
								sentence-transformers
							{:else if emb.backend === 'chromadb_default'}
								ChromaDB built-in (onnxruntime)
							{:else}
								{emb.backend}
							{/if}
						</div>
					</div>
				</div>
			</div>
		{/if}

		<!-- n8n process control -->
		<div class="mt-3 {cardClass}">
			<div class="flex items-center justify-between">
				<div>
					<div class="text-[10px] uppercase tracking-wider text-surface-400">n8n Process</div>
					<div class="mt-1 text-sm">
						<span class={n8nProcessStatus === 'running' ? 'text-green-400' : n8nProcessStatus === 'starting' ? 'text-yellow-400' : 'text-surface-400'}>
							{n8nProcessStatus}
						</span>
					</div>
				</div>
				<div class="flex gap-2">
					{#if n8nProcessStatus !== 'running' && n8nProcessStatus !== 'starting'}
						<button
							class="rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-green-500 disabled:opacity-50"
							onclick={startN8n}
							disabled={!!n8nAction}
						>
							{n8nAction === 'starting' ? 'Starting...' : 'Start'}
						</button>
					{:else if n8nProcessStatus === 'running'}
						<button
							class="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
							onclick={stopN8n}
							disabled={!!n8nAction}
						>
							{n8nAction === 'stopping' ? 'Stopping...' : 'Stop'}
						</button>
					{/if}
				</div>
			</div>
		</div>

		<!-- Last WS message -->
		{#if $lastMessage}
			<div class="mt-3 {cardClass}">
				<div class="mb-1.5 text-[10px] uppercase tracking-wider text-surface-400">Last WS Message</div>
				<pre class="overflow-x-auto text-xs text-surface-300">{JSON.stringify($lastMessage, null, 2)}</pre>
			</div>
		{/if}
	</section>

	<!-- Analytics section -->
	<section>
		<div class="mb-4 flex items-center justify-between">
			<h2 class="text-lg font-semibold">Analytics</h2>
			<select
				bind:value={days}
				onchange={loadDashboard}
				class="rounded-lg border px-3 py-1.5 text-sm text-surface-200 focus:border-laya-orange/50 focus:outline-none {$glassTheme ? 'glass-input' : 'border-surface-600 bg-surface-800'}"
			>
				<option value={7}>Last 7 days</option>
				<option value={14}>Last 14 days</option>
				<option value={30}>Last 30 days</option>
				<option value={90}>Last 90 days</option>
			</select>
		</div>

		{#if dashLoading}
			<div class="flex items-center justify-center py-12">
				<div class="h-5 w-5 animate-spin rounded-full border-2 border-laya-orange border-t-transparent"></div>
			</div>
		{:else if dashError}
			<div class="rounded-xl border border-red-800 bg-red-900/20 p-4 text-sm text-red-300">
				{dashError}
			</div>
		{:else if dashboard}
			<!-- Top-level stats -->
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
				<StatCard label="Events Processed" value={String(dashboard.stats.events_processed)} />
				<StatCard
					label="Events Filtered"
					value={String(dashboard.stats.events_filtered)}
					subtitle="{dashboard.stats.events_processed > 0 ? Math.round((dashboard.stats.events_filtered / dashboard.stats.events_processed) * 100) : 0}% filter rate"
				/>
				<StatCard
					label="Cards Generated"
					value={String(dashboard.stats.cards_generated)}
					color="text-blue-400"
				/>
				<StatCard
					label="Actions Executed"
					value={String(dashboard.stats.actions_executed)}
					color="text-emerald-400"
				/>
				<StatCard
					label="Time Saved (BETA)"
					value={formatTime(dashboard.time_saved.total_minutes)}
					color="text-amber-400"
				/>
			</div>

			<!-- Cost + Response Time row -->
			<div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
				<StatCard
					label="LLM Cost"
					value={formatCost(dashboard.llm_costs.total_cost_usd)}
					subtitle="{formatTokens(dashboard.llm_costs.total_input_tokens)} in / {formatTokens(dashboard.llm_costs.total_output_tokens)} out"
				/>
				<StatCard
					label="Avg Response"
					value="{Math.round(dashboard.response_time.avg_ms)}ms"
					subtitle="p50: {Math.round(dashboard.response_time.p50_ms)}ms / p95: {Math.round(dashboard.response_time.p95_ms)}ms"
				/>
				<StatCard
					label="Approval Rate"
					value="{dashboard.stats.cards_approved + dashboard.stats.cards_dismissed > 0 ? Math.round((dashboard.stats.cards_approved / (dashboard.stats.cards_approved + dashboard.stats.cards_dismissed)) * 100) : 0}%"
					subtitle="{dashboard.stats.cards_approved} approved / {dashboard.stats.cards_dismissed} dismissed"
					color="text-green-400"
				/>
			</div>

			<!-- Charts row -->
			<div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
				<DonutChart data={cardStatusData} title="Cards by Status" />
				<DonutChart data={actionStatusData} title="Actions by Status" />
			</div>

			{#if sourceData.length > 0}
				<div class="mt-3">
					<BarChart data={sourceData} title="Events by Source" />
				</div>
			{/if}

			{#if dashboard.llm_costs.by_feature && Object.keys(dashboard.llm_costs.by_feature).length > 0}
				<div id="cost" class="mt-3">
					<FeatureCostChart
						byFeature={dashboard.llm_costs.by_feature}
						byStep={dashboard.llm_costs.by_step}
					/>
				</div>
			{/if}

			{#if costByModel.length > 0}
				<div class="mt-3">
					<BarChart data={costByModel} title="LLM Cost by Model ($)" />
				</div>
			{/if}

			{#if approvalData.length > 0}
				<div class="mt-3">
					<BarChart data={approvalData} title="Approval Rate by Persona (%)" />
				</div>
			{/if}
		{/if}
	</section>
</div>
