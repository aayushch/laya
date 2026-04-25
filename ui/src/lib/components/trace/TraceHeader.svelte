<script lang="ts">
	import type { TraceCluster } from '$lib/api/types';
	import { traceNarrativeMap, traceNarrativeStreamingMap } from '$lib/stores/trace';
	import { pendingCardId } from '$lib/stores/chat';
	import { goto } from '$app/navigation';
	import { slide } from 'svelte/transition';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { portal } from '$lib/actions/portal';
	import TraceTimeline from './TraceTimeline.svelte';

	let {
		cluster,
		traceId,
		onremove,
		ongenerate,
		isLast = false,
		expandAll = null
	}: {
		cluster: TraceCluster;
		traceId: string;
		onremove?: () => void;
		ongenerate?: () => void;
		isLast?: boolean;
		expandAll?: boolean | null;
	} = $props();

	let expanded = $state(false);

	// React to expand all / collapse all signal from parent
	$effect(() => {
		if (expandAll !== null && expandAll !== undefined) {
			expanded = expandAll;
		}
	});
	let allCardsExpanded = $state<boolean | null>(null);
	let cardsExpanded = $state(false);

	// Tooltip state
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}

	function hideTooltip() { tooltip = null; }

	function parseNarrative(content: string, streaming: boolean) {
		if (!content) return { thinking: null, response: content, isThinking: false };
		const match = content.match(/<think>([\s\S]*?)<\/think>/);
		if (match) {
			return {
				thinking: match[1].trim(),
				response: content.slice(match.index! + match[0].length).trim(),
				isThinking: false
			};
		}
		if (content.includes('<think>')) {
			if (streaming) {
				return { thinking: content.replace('<think>', '').trim(), response: '', isThinking: true };
			}
			return { thinking: null, response: content.replace('<think>', ''), isThinking: false };
		}
		return { thinking: null, response: content, isThinking: false };
	}

	// Narrative map keys are scoped by trace_id (see coherence/+page.svelte) so
	// in-flight streaming state survives Back/re-select without colliding across traces.
	const narrativeMapKey = $derived(`${traceId}:${cluster.cluster_id}`);
	const isStreaming = $derived($traceNarrativeStreamingMap[narrativeMapKey] ?? false);
	const streamedNarrative = $derived($traceNarrativeMap[narrativeMapKey] ?? '');
	const narrativeText = $derived(streamedNarrative || cluster.narrative || '');
	const parsed = $derived(parseNarrative(narrativeText, isStreaming));
	const hasNarrative = $derived(!!narrativeText || isStreaming);

	const latestCardId = $derived(cluster.timeline.length > 0 ? cluster.timeline[cluster.timeline.length - 1].card_id : null);

	function viewInFeed() {
		if (latestCardId) {
			pendingCardId.set(latestCardId);
			goto('/feed');
		}
	}

	const platformBadgeColors: Record<string, string> = {
		jira: 'bg-blue-500/15 text-blue-400 border-blue-500/25',
		github: 'bg-purple-500/15 text-purple-400 border-purple-500/25',
		bitbucket: 'bg-sky-500/15 text-sky-400 border-sky-500/25',
		slack: 'bg-green-500/15 text-green-400 border-green-500/25',
		gmail: 'bg-red-500/15 text-red-400 border-red-500/25',
		calendar: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/25'
	};

	function formatDate(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso + 'T00:00:00');
		const day = d.getDate();
		const month = d.toLocaleString(undefined, { month: 'short' });
		const year = d.getFullYear();
		const suffix = [11, 12, 13].includes(day % 100)
			? 'th'
			: ['th', 'st', 'nd', 'rd'][day % 10] ?? 'th';
		return `${day}${suffix} ${month} ${year}`;
	}

	const dateRangeText = $derived.by(() => {
		const from = cluster.status_summary.date_range.from;
		const to = cluster.status_summary.date_range.to;
		if (!from) return '';
		const f = formatDate(from);
		if (!to || to === from) return f;
		// If same year + month, abbreviate: "24th — 30th Mar 2026"
		const fd = new Date(from + 'T00:00:00');
		const td = new Date(to + 'T00:00:00');
		const t = formatDate(to);
		if (fd.getFullYear() === td.getFullYear() && fd.getMonth() === td.getMonth()) {
			const fDay = fd.getDate();
			const fSuffix = [11, 12, 13].includes(fDay % 100)
				? 'th'
				: ['th', 'st', 'nd', 'rd'][fDay % 10] ?? 'th';
			return `${fDay}${fSuffix} — ${t}`;
		}
		if (fd.getFullYear() === td.getFullYear()) {
			// Same year: "24th Mar — 2nd Apr 2026"
			const fDay = fd.getDate();
			const fSuffix = [11, 12, 13].includes(fDay % 100)
				? 'th'
				: ['th', 'st', 'nd', 'rd'][fDay % 10] ?? 'th';
			const fMonth = fd.toLocaleString(undefined, { month: 'short' });
			return `${fDay}${fSuffix} ${fMonth} — ${t}`;
		}
		return `${f} — ${t}`;
	});
</script>

{#if tooltip}
	<div
		use:portal
		class="fixed z-[100] px-2 py-1 rounded-md border border-transparent glass-tooltip text-[10px] font-medium pointer-events-none -translate-x-1/2 -translate-y-full max-w-[400px] break-words"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

<div class="relative pl-7">
	<!-- Vertical trunk segment (non-last: full height; last: to row center only) -->
	{#if !isLast}
		<div class="absolute left-[11px] top-0 bottom-0 w-px bg-surface-700"></div>
	{:else}
		<div class="absolute left-[11px] top-0 w-px bg-surface-700" style="height: 11px"></div>
	{/if}

	<!-- Horizontal branch: from trunk through [+] box to content -->
	<div class="absolute left-[11px] top-[11px] w-[17px] h-px bg-surface-700"></div>

	<!-- [+] box sits on the horizontal line -->
	<button
		onclick={() => (expanded = !expanded)}
		class="absolute left-[5px] top-[4px] w-[13px] h-[13px] flex items-center justify-center
		       border border-surface-600 bg-surface-800 hover:border-surface-500
		       text-surface-400 hover:text-surface-200 transition-colors cursor-pointer z-10"
	>
		<svg class="w-[7px] h-[7px]" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 12 12">
			{#if expanded}
				<line x1="1" y1="6" x2="11" y2="6" />
			{:else}
				<line x1="1" y1="6" x2="11" y2="6" />
				<line x1="6" y1="1" x2="6" y2="11" />
			{/if}
		</svg>
	</button>

	<!-- CLUSTER ROW -->
	<div class="h-[22px] flex items-center group -mx-2 px-2 rounded hover:bg-surface-700/30 relative">
		<!-- Platform badges (fixed) -->
		<span class="flex items-center gap-1 shrink-0 w-[80px]">
			{#each cluster.status_summary.platforms_involved as platform}
				<span class="inline-flex items-center px-1.5 py-0 rounded border text-[9px] font-medium capitalize leading-[16px] {platformBadgeColors[platform] || 'bg-surface-700/50 text-surface-400 border-surface-600/50'}">
					{platform}
				</span>
			{/each}
		</span>

		<!-- Title (fills remaining space, truncates) — tooltip shows full title on hover -->
		<button
			onclick={() => (expanded = !expanded)}
			onmouseenter={(e) => showTooltip(e, cluster.primary_entity.title)}
			onmouseleave={hideTooltip}
			class="text-[13px] font-medium text-surface-100 hover:text-laya-orange transition-colors truncate text-left cursor-pointer flex-1 min-w-0"
		>
			{cluster.primary_entity.title}
		</button>

		<!-- Meta: compact card-count badge + date range. The badge stays visible at all
		     times (it's persistent metadata). Only the date fades on hover so the action
		     overlay below can occupy the date's column without hiding the badge.
		     Fixed-width sub-columns keep badges and date starts aligned across rows. -->
		<span class="flex items-center gap-2 shrink-0 ml-2">
			<span class="w-[24px] flex justify-end">
				<span
					class="inline-flex items-center justify-center min-w-[22px] h-[16px] px-1.5 rounded-full
					       bg-surface-700/60 border border-surface-600/50
					       text-[10px] font-medium text-surface-300 tabular-nums leading-none"
					aria-label="{cluster.status_summary.total_cards} cards"
				>
					{cluster.status_summary.total_cards}
				</span>
			</span>
			<span
				class="text-[10px] text-surface-500 whitespace-nowrap tabular-nums w-[160px] text-right
				       transition-opacity duration-150 group-hover:opacity-0 group-hover:pointer-events-none"
			>
				{dateRangeText}
			</span>
		</span>

		<!-- Action buttons — fade into the meta's spot on hover; no hard bg overlay. -->
		<span class="absolute right-2 top-0 h-full flex items-center gap-1
		             opacity-0 pointer-events-none
		             group-hover:opacity-100 group-hover:pointer-events-auto
		             transition-opacity duration-150 z-10">
			{#if hasNarrative}
				<span class="text-[10px] text-laya-orange" role="img" aria-label="Has narrative"
					onmouseenter={(e) => showTooltip(e, 'Has narrative')}
					onmouseleave={hideTooltip}
				>✦</span>
			{:else if ongenerate}
				<button
					aria-label="Generate narrative"
					onclick={(e) => { e.stopPropagation(); ongenerate?.(); }}
					onmouseenter={(e) => showTooltip(e, 'Generate narrative')}
					onmouseleave={hideTooltip}
					class="p-1 rounded text-surface-500 hover:text-laya-orange hover:bg-surface-700/50 transition-colors cursor-pointer"
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
					</svg>
				</button>
			{/if}

			{#if cluster.primary_entity.url}
				<a
					href={cluster.primary_entity.url}
					target="_blank"
					rel="noopener noreferrer"
					aria-label="Open in platform"
					onmouseenter={(e) => showTooltip(e, 'Open in platform')}
					onmouseleave={hideTooltip}
					class="p-1 rounded text-surface-500 hover:text-laya-orange hover:bg-surface-700/50 transition-colors"
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-4.5-6H18m0 0v4.5m0-4.5L10.5 13.5" />
					</svg>
				</a>
			{/if}

			{#if latestCardId}
				<button
					aria-label="View in feed"
					onclick={(e) => { e.stopPropagation(); viewInFeed(); }}
					onmouseenter={(e) => showTooltip(e, 'View in feed')}
					onmouseleave={hideTooltip}
					class="p-1 rounded text-surface-500 hover:text-laya-orange hover:bg-surface-700/50 transition-colors cursor-pointer"
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
					</svg>
				</button>
			{/if}

			{#if onremove}
				<button
					aria-label="Remove cluster"
					onclick={(e) => { e.stopPropagation(); onremove?.(); }}
					onmouseenter={(e) => showTooltip(e, 'Remove cluster')}
					onmouseleave={hideTooltip}
					class="p-1 rounded text-surface-500 hover:text-red-400 hover:bg-surface-700/50 transition-colors cursor-pointer"
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			{/if}
		</span>
	</div>

	<!-- EXPANDED CHILDREN -->
	{#if expanded}
		<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }}>
			<!--
			  Sub-tree children. Each child uses pl-5 (20px indent).
			  DOT: w-1.5 h-1.5 (6px). Horizontal branch ends at dot center.
			  For a row centered at Y: line at Y, dot at Y-3, branch w-[13px].
			  Single-line rows (h-[22px]): Y = 11. Multi-line: Y = pad + 11.
			-->
			<div class="relative pl-4 space-y-px">
				<!-- Narrative — callout pill -->
				{#if hasNarrative}
					<div class="relative pl-5 py-1">
						<div class="absolute left-0 top-0 h-[15px] w-px bg-surface-700/40"></div>
						<div class="absolute left-0 top-[15px] w-[13px] h-px bg-surface-700/40"></div>
						<div class="absolute left-[10px] top-[12px] w-1.5 h-1.5 rounded-full bg-laya-orange/50"></div>
						<div class="absolute left-0 top-[15px] bottom-0 w-px bg-surface-700/40"></div>

						{#if parsed.isThinking || (isStreaming && !parsed.response && !parsed.thinking)}
							<!-- Streaming has started but no content has arrived yet (or we're
							     still inside <think>) — render a spinner so the user knows
							     generation is in progress. Without this, parseNarrative('')
							     short-circuits to isThinking=false and the pill renders empty. -->
							<div class="rounded-md bg-laya-orange/5 border border-laya-orange/15 px-3 py-2">
								<div class="flex items-center gap-1.5 text-[11px] text-surface-400">
									<svg class="h-2.5 w-2.5 animate-spin text-laya-orange/60 shrink-0" fill="none" viewBox="0 0 24 24">
										<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
										<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
									</svg>
									Generating narrative...
								</div>
							</div>
						{:else}
							<div class="rounded-md bg-laya-orange/5 border border-laya-orange/15 px-3 py-2">
								<div class="flex items-center gap-1.5 mb-1">
									<span class="text-[9px] font-medium uppercase tracking-wider text-laya-orange/70">✦ narrative</span>
									{#if isStreaming}
										<svg class="h-2.5 w-2.5 animate-spin text-laya-orange/60 shrink-0" fill="none" viewBox="0 0 24 24">
											<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
											<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
										</svg>
									{/if}
								</div>
								{#if parsed.thinking}
									<details>
										<summary class="cursor-pointer text-[10px] text-surface-500 hover:text-surface-300 select-none list-none flex items-center gap-1 mb-1">
											<svg class="w-2.5 h-2.5 shrink-0 transition-transform [[open]>&]:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
												<path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
											</svg>
											thought process
										</summary>
										<div class="ml-3.5 border-l border-laya-orange/10 pl-2 text-[10px] leading-relaxed text-surface-500 whitespace-pre-wrap mb-1.5">
											{parsed.thinking}
										</div>
									</details>
								{/if}
								{#if parsed.response}
									<p class="text-laya-secondary text-surface-200 leading-relaxed italic">
										{parsed.response}{#if isStreaming && !parsed.isThinking}<span class="inline-block w-1 h-3 bg-laya-orange/70 animate-pulse ml-0.5 align-middle"></span>{/if}
									</p>
								{/if}
							</div>
						{/if}
					</div>
				{:else if ongenerate}
					<!-- min-h-[22px] row, Y=11 -->
					<div class="relative pl-5 min-h-[22px] flex items-center">
						<div class="absolute left-0 top-0 h-[11px] w-px bg-surface-700/40"></div>
						<div class="absolute left-0 top-[11px] w-[13px] h-px bg-surface-700/40"></div>
						<div class="absolute left-[10px] top-[8px] w-1.5 h-1.5 rounded-full bg-surface-600"></div>
						<div class="absolute left-0 top-[11px] bottom-0 w-px bg-surface-700/40"></div>
						<button
							onclick={ongenerate}
							class="flex items-center gap-1 text-[11px] text-surface-500 hover:text-laya-orange transition-colors cursor-pointer"
						>
							<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
							</svg>
							generate narrative
						</button>
					</div>
				{/if}

				<!-- Linked entities -->
				{#if cluster.linked_entities.length > 0}
					<!-- min-h-[22px], py-0.5=2px top, Y=2+11=13 -->
					<div class="relative pl-5 min-h-[22px] flex items-center py-0.5">
						<div class="absolute left-0 top-0 h-[13px] w-px bg-surface-700/40"></div>
						<div class="absolute left-0 top-[13px] w-[13px] h-px bg-surface-700/40"></div>
						<div class="absolute left-[10px] top-[10px] w-1.5 h-1.5 rounded-full bg-surface-600"></div>
						<div class="absolute left-0 top-[13px] bottom-0 w-px bg-surface-700/40"></div>
						<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
							<span class="text-surface-600">linked:</span>
							{#each cluster.linked_entities as entity}
								{#if entity.url}
									<a href={entity.url} target="_blank" rel="noopener noreferrer"
										class="text-surface-400 hover:text-laya-orange transition-colors">
										{entity.title}
									</a>
								{:else}
									<span class="text-surface-400">{entity.title}</span>
								{/if}
							{/each}
						</div>
					</div>
				{/if}

				<!-- Expand/collapse all control -->
				{#if cluster.timeline.length > 1}
					<div class="relative pl-5 h-[22px] flex items-center justify-end pr-2">
						<div class="absolute left-0 top-0 bottom-0 w-px bg-surface-700/40"></div>
						<div class="flex items-center gap-2 text-[10px] text-surface-500">
							{#if cardsExpanded}
								<button
									onclick={() => { allCardsExpanded = false; cardsExpanded = false; }}
									class="hover:text-laya-orange transition-colors cursor-pointer"
								>collapse all</button>
							{:else}
								<button
									onclick={() => { allCardsExpanded = true; cardsExpanded = true; }}
									class="hover:text-laya-orange transition-colors cursor-pointer"
								>expand all</button>
							{/if}
						</div>
					</div>
				{/if}

				<!-- Timeline cards -->
				<div class="relative">
					<div class="absolute left-0 top-0 bottom-0 w-px bg-surface-700/40"></div>
					<TraceTimeline {cluster} compact expandAll={allCardsExpanded} />
				</div>

				<!-- Status footer (last child — no line below) -->
				<!-- min-h-[22px], py-0.5=2px top, Y=13 -->
				<div class="relative pl-5 min-h-[22px] flex items-center py-0.5 mb-1">
					<div class="absolute left-0 top-0 h-[13px] w-px bg-surface-700/40"></div>
					<div class="absolute left-0 top-[13px] w-[13px] h-px bg-surface-700/40"></div>
					<div class="absolute left-[10px] top-[10px] w-1.5 h-1.5 rounded-full bg-surface-500"></div>
					<span class="text-[10px] text-surface-600 italic">
						{cluster.status_summary.current_state}
					</span>
				</div>
			</div>
		</div>
	{/if}
</div>
