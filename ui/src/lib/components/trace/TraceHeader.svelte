<script lang="ts">
	import type { TraceCluster } from '$lib/api/types';
	import { traceNarrativeMap, traceNarrativeStreamingMap } from '$lib/stores/trace';
	import { pendingCardId } from '$lib/stores/chat';
	import { goto } from '$app/navigation';

	let {
		cluster,
		onexport,
		onrerun,
		onremove,
		ongenerate
	}: {
		cluster: TraceCluster;
		onexport?: () => void;
		onrerun?: () => void;
		onremove?: () => void;
		ongenerate?: () => void;
	} = $props();

	// Tooltip state
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}

	function hideTooltip() { tooltip = null; }

	// Parse <think>...</think> blocks from narrative text
	function parseNarrative(content: string, streaming: boolean) {
		if (!content) return { thinking: null, response: content, isThinking: false };

		// Complete thinking block
		const match = content.match(/<think>([\s\S]*?)<\/think>/);
		if (match) {
			return {
				thinking: match[1].trim(),
				response: content.slice(match.index! + match[0].length).trim(),
				isThinking: false
			};
		}

		// Open <think> tag with no closing — still thinking
		if (content.includes('<think>')) {
			if (streaming) {
				return {
					thinking: content.replace('<think>', '').trim(),
					response: '',
					isThinking: true
				};
			}
			return { thinking: null, response: content.replace('<think>', ''), isThinking: false };
		}

		return { thinking: null, response: content, isThinking: false };
	}

	const isStreaming = $derived($traceNarrativeStreamingMap[cluster.cluster_id] ?? false);
	const streamedNarrative = $derived($traceNarrativeMap[cluster.cluster_id] ?? '');
	const narrativeText = $derived(streamedNarrative || cluster.narrative || '');
	const parsed = $derived(parseNarrative(narrativeText, isStreaming));
	const hasNarrative = $derived(!!narrativeText || isStreaming);

	// Latest card ID for "View in feed" link (timeline is chronological, last = most recent)
	const latestCardId = $derived(cluster.timeline.length > 0 ? cluster.timeline[cluster.timeline.length - 1].card_id : null);

	function viewInFeed() {
		if (latestCardId) {
			pendingCardId.set(latestCardId);
			goto('/feed');
		}
	}

	const platformColors: Record<string, string> = {
		jira: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
		github: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
		bitbucket: 'bg-sky-500/20 text-sky-400 border-sky-500/30',
		slack: 'bg-green-500/20 text-green-400 border-green-500/30',
		gmail: 'bg-red-500/20 text-red-400 border-red-500/30',
		calendar: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
	};
</script>

<!-- Fixed-position tooltip -->
{#if tooltip}
	<div
		class="fixed z-50 px-2.5 py-1 rounded-md bg-surface-700 text-surface-100 text-xs font-medium shadow-lg pointer-events-none -translate-x-1/2 -translate-y-full"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

<div class="rounded-xl border border-surface-700 bg-surface-800/80 px-4 py-3">
	<!-- Entity title and source link -->
	<div class="flex items-center justify-between gap-3 mb-2">
		<div class="flex-1 min-w-0">
			{#if cluster.primary_entity.url}
				<a
					href={cluster.primary_entity.url}
					target="_blank"
					rel="noopener noreferrer"
					class="text-base font-semibold text-surface-50 hover:text-laya-orange transition-colors"
				>
					{cluster.primary_entity.title}
					<svg class="inline w-3.5 h-3.5 ml-1 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-4.5-6H18m0 0v4.5m0-4.5L10.5 13.5" />
					</svg>
				</a>
			{:else}
				<h2 class="text-base font-semibold text-surface-50">{cluster.primary_entity.title}</h2>
			{/if}
		</div>

		<!-- Action buttons -->
		<div class="flex items-center gap-1 shrink-0">
			{#if onrerun}
				<button
					onclick={onrerun}
					onmouseenter={(e) => showTooltip(e, 'Re-run trace')}
					onmouseleave={hideTooltip}
					aria-label="Re-run trace"
					class="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
					</svg>
				</button>
			{/if}
			{#if onexport}
				<button
					onclick={onexport}
					onmouseenter={(e) => showTooltip(e, 'Export cluster')}
					onmouseleave={hideTooltip}
					aria-label="Export cluster"
					class="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
					</svg>
				</button>
			{/if}
			{#if onremove}
				<button
					onclick={onremove}
					onmouseenter={(e) => showTooltip(e, 'Remove cluster')}
					onmouseleave={hideTooltip}
					aria-label="Remove cluster"
					class="p-2 rounded-lg text-surface-400 hover:text-red-400 hover:bg-surface-700 transition-colors"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			{/if}
		</div>
	</div>

	<!-- Platform badges and stats -->
	<div class="flex flex-wrap items-center gap-2 mb-3">
		{#each cluster.status_summary.platforms_involved as platform}
			<span class="inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium capitalize {platformColors[platform] || 'bg-surface-700 text-surface-300 border-surface-600'}">
				{platform}
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
	{#if hasNarrative}
		<div class="rounded-lg bg-surface-900/60 border border-surface-700/50 px-3.5 py-3">
			<!-- Thinking: collapsed or streaming indicator -->
			{#if parsed.isThinking}
				<div class="flex items-center gap-1.5 text-[11px] font-medium text-surface-400">
					<svg class="h-3 w-3 animate-spin text-surface-500" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
					</svg>
					Generating narrative...
				</div>
			{:else if parsed.thinking}
				<details class="mb-3">
					<summary class="cursor-pointer text-[11px] font-medium text-surface-500 hover:text-surface-300 select-none">
						Thought process
					</summary>
					<div class="mt-1.5 border-l-2 border-surface-600 pl-2.5 text-[11px] leading-relaxed text-surface-500 whitespace-pre-wrap">
						{parsed.thinking}
					</div>
				</details>
			{/if}

			<!-- Main narrative response -->
			{#if parsed.response}
				<p class="text-surface-200 text-sm leading-relaxed">
					{parsed.response}{#if isStreaming && !parsed.isThinking}<span class="inline-block w-1.5 h-4 bg-laya-orange/70 animate-pulse ml-0.5 align-middle"></span>{/if}
				</p>
			{:else if isStreaming && !parsed.isThinking}
				<!-- Streaming but no response text yet (and not in thinking mode) -->
				<div class="flex items-center gap-2 text-surface-500 text-sm">
					<span class="inline-block w-1.5 h-4 bg-laya-orange/70 animate-pulse"></span>
				</div>
			{/if}
		</div>
	{:else}
		<!-- No narrative yet — show generate button -->
		<button
			onclick={ongenerate}
			class="w-full rounded-lg border border-dashed border-surface-600 bg-surface-900/40 px-4 py-3
			       flex items-center justify-center gap-2 text-sm text-surface-400
			       hover:border-laya-orange/40 hover:text-laya-orange hover:bg-laya-orange/5 transition-colors cursor-pointer"
		>
			<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
				<path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
			</svg>
			Generate Narrative
		</button>
	{/if}

	<!-- View in feed link -->
	{#if latestCardId}
		<div class="mt-3 flex justify-end">
			<button
				onclick={viewInFeed}
				class="inline-flex items-center gap-1.5 text-xs text-laya-orange hover:text-laya-gold transition-colors"
			>
				View in feed
				<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
				</svg>
			</button>
		</div>
	{/if}
</div>
