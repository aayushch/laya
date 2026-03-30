<script lang="ts">
	import type { TraceCluster } from '$lib/api/types';
	import { traceNarrative, traceNarrativeStreaming } from '$lib/stores/trace';

	let {
		cluster,
		onexport,
		onrerun
	}: {
		cluster: TraceCluster;
		onexport?: () => void;
		onrerun?: () => void;
	} = $props();

	const platformIcons: Record<string, string> = {
		jira: 'J',
		github: 'GH',
		bitbucket: 'BB',
		slack: 'SL',
		gmail: 'GM',
		calendar: 'CA'
	};

	const platformColors: Record<string, string> = {
		jira: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
		github: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
		bitbucket: 'bg-sky-500/20 text-sky-400 border-sky-500/30',
		slack: 'bg-green-500/20 text-green-400 border-green-500/30',
		gmail: 'bg-red-500/20 text-red-400 border-red-500/30',
		calendar: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
	};
</script>

<div class="rounded-xl border border-surface-700 bg-surface-800/80 p-5">
	<!-- Entity title and source link -->
	<div class="flex items-start justify-between gap-3 mb-3">
		<div class="flex-1 min-w-0">
			{#if cluster.primary_entity.url}
				<a
					href={cluster.primary_entity.url}
					target="_blank"
					rel="noopener noreferrer"
					class="text-xl font-semibold text-surface-50 hover:text-laya-orange transition-colors"
				>
					{cluster.primary_entity.title}
					<svg class="inline w-4 h-4 ml-1 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-4.5-6H18m0 0v4.5m0-4.5L10.5 13.5" />
					</svg>
				</a>
			{:else}
				<h2 class="text-xl font-semibold text-surface-50">{cluster.primary_entity.title}</h2>
			{/if}
		</div>

		<!-- Action buttons -->
		<div class="flex items-center gap-2 shrink-0">
			{#if onrerun}
				<button
					onclick={onrerun}
					class="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
					title="Re-run trace"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
					</svg>
				</button>
			{/if}
			{#if onexport}
				<button
					onclick={onexport}
					class="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
					title="Export as Markdown"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
					</svg>
				</button>
			{/if}
		</div>
	</div>

	<!-- Platform badges and stats -->
	<div class="flex flex-wrap items-center gap-2 mb-4">
		{#each cluster.status_summary.platforms_involved as platform}
			<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs font-medium {platformColors[platform] || 'bg-surface-700 text-surface-300 border-surface-600'}">
				{platformIcons[platform] || platform.slice(0, 2).toUpperCase()}
				<span class="capitalize">{platform}</span>
			</span>
		{/each}

		<span class="text-surface-500 text-xs mx-1">|</span>

		<span class="text-surface-400 text-xs">
			{cluster.status_summary.date_range.from} — {cluster.status_summary.date_range.to}
		</span>

		<span class="text-surface-500 text-xs mx-1">|</span>

		<span class="text-surface-400 text-xs">
			{cluster.status_summary.total_cards} events
		</span>

		{#if cluster.status_summary.pending_actions > 0}
			<span class="text-surface-500 text-xs mx-1">|</span>
			<span class="text-laya-orange text-xs font-medium">
				{cluster.status_summary.pending_actions} pending
			</span>
		{/if}

		{#if cluster.linked_entities.length > 0}
			<span class="text-surface-500 text-xs mx-1">|</span>
			<span class="text-surface-400 text-xs">
				+ {cluster.linked_entities.length} linked {cluster.linked_entities.length === 1 ? 'entity' : 'entities'}
			</span>
		{/if}
	</div>

	<!-- Narrative -->
	<div class="rounded-lg bg-surface-900/60 border border-surface-700/50 p-4">
		{#if $traceNarrative || $traceNarrativeStreaming}
			<p class="text-surface-200 text-sm leading-relaxed">
				{$traceNarrative || cluster.narrative || ''}{#if $traceNarrativeStreaming}<span class="inline-block w-1.5 h-4 bg-laya-orange/70 animate-pulse ml-0.5 align-middle"></span>{/if}
			</p>
		{:else if cluster.narrative}
			<p class="text-surface-200 text-sm leading-relaxed">{cluster.narrative}</p>
		{:else}
			<div class="flex items-center gap-2 text-surface-500 text-sm">
				<svg class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
					<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" opacity="0.25" />
					<path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
				</svg>
				Generating narrative...
			</div>
		{/if}
	</div>
</div>
