<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { DashboardResponse } from '$lib/api/types';
	import StatCard from '$lib/components/dashboard/StatCard.svelte';
	import BarChart from '$lib/components/dashboard/BarChart.svelte';
	import DonutChart from '$lib/components/dashboard/DonutChart.svelte';

	let dashboard: DashboardResponse | null = $state(null);
	let loading = $state(true);
	let error = $state('');
	let days = $state(30);

	async function loadDashboard() {
		loading = true;
		error = '';
		try {
			dashboard = await engineApi.getDashboard(days);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load dashboard';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadDashboard();
	});

	const cardStatusData = $derived(
		dashboard
			? [
					{ label: 'Pending', value: dashboard.stats.cards_pending, color: '#facc15' },
					{ label: 'Approved', value: dashboard.stats.cards_approved, color: '#4ade80' },
					{ label: 'Dismissed', value: dashboard.stats.cards_dismissed, color: '#64748b' }
				]
			: []
	);

	const actionStatusData = $derived(
		dashboard
			? [
					{ label: 'Completed', value: dashboard.stats.actions_completed, color: '#4ade80' },
					{ label: 'Failed', value: dashboard.stats.actions_failed, color: '#f87171' },
					{
						label: 'In Progress',
						value: Math.max(
							0,
							dashboard.stats.actions_executed -
								dashboard.stats.actions_completed -
								dashboard.stats.actions_failed
						),
						color: '#60a5fa'
					}
				]
			: []
	);

	const sourceData = $derived(
		dashboard
			? dashboard.events_by_source.map((s) => ({ label: s.source, value: s.count }))
			: []
	);

	const approvalData = $derived(
		dashboard
			? dashboard.approval_by_persona.map((p) => ({
					label: p.persona,
					value: Math.round(p.rate * 100)
				}))
			: []
	);

	const costByModel = $derived(
		dashboard
			? Object.entries(dashboard.llm_costs.by_model).map(([label, value]) => ({
					label,
					value: Math.round(value * 1000) / 1000
				}))
			: []
	);

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
</script>

<svelte:head>
	<title>Dashboard - Laya</title>
</svelte:head>

<div class="mx-auto max-w-6xl space-y-6">
	<div class="flex items-center justify-between">
		<h2 class="text-lg font-semibold">Dashboard</h2>
		<div class="flex items-center gap-2">
			<select
				bind:value={days}
				onchange={loadDashboard}
				class="rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-sm text-surface-200 focus:border-blue-500 focus:outline-none"
			>
				<option value={7}>Last 7 days</option>
				<option value={14}>Last 14 days</option>
				<option value={30}>Last 30 days</option>
				<option value={90}>Last 90 days</option>
			</select>
		</div>
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-20">
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-blue-400 border-t-transparent"></div>
		</div>
	{:else if error}
		<div class="rounded-xl border border-red-800 bg-red-900/20 p-4 text-sm text-red-300">
			{error}
		</div>
	{:else if dashboard}
		<!-- Top-level stats -->
		<div class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
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
				label="Time Saved"
				value={formatTime(dashboard.time_saved.total_minutes)}
				color="text-amber-400"
			/>
		</div>

		<!-- Cost + Response Time row -->
		<div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
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
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			<DonutChart data={cardStatusData} title="Cards by Status" />
			<DonutChart data={actionStatusData} title="Actions by Status" />
		</div>

		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			{#if sourceData.length > 0}
				<BarChart data={sourceData} title="Events by Source" />
			{/if}
			{#if approvalData.length > 0}
				<BarChart data={approvalData} title="Approval Rate by Persona (%)" />
			{/if}
		</div>

		{#if costByModel.length > 0}
			<BarChart data={costByModel} title="LLM Cost by Model ($)" />
		{/if}
	{/if}
</div>
