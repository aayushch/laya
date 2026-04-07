<script lang="ts">
	import type { TraceCluster } from '$lib/api/types';
	import TraceCard from './TraceCard.svelte';

	let {
		cluster,
		compact = false,
		expandAll = null
	}: {
		cluster: TraceCluster;
		compact?: boolean;
		expandAll?: boolean | null;
	} = $props();

	let cardsById = $derived(
		Object.fromEntries(cluster.timeline.map((c) => [c.card_id, c]))
	);
</script>

{#if compact}
	<!--
	  Compact tree: a single vertical line runs through all children (chapters + cards).
	  Each chapter is a labeled node; its cards are nested one level deeper.
	  The wrapping div is relative with pl-5 so the parent's sub-tree line connects.
	-->
	{#if cluster.chapters.length > 0}
		<!-- Wrapper with continuous vertical line for all chapters -->
		<div class="relative">
			{#each cluster.chapters as chapter, ci}
				<!-- Chapter label row -->
				<div class="relative pl-5 h-[22px] flex items-center">
					<!-- Vertical line through this row (full for non-last, partial for last) -->
					{#if ci < cluster.chapters.length - 1}
						<div class="absolute left-0 top-0 bottom-0 w-px bg-surface-700/40"></div>
					{:else}
						<div class="absolute left-0 top-0 w-px bg-surface-700/40" style="height: 11px"></div>
					{/if}
					<!-- Horizontal branch + dot: Y=11, dot center=11 → top=8 for 6px dot -->
					<div class="absolute left-0 top-[11px] w-[13px] h-px bg-surface-700/40"></div>
					<div class="absolute left-[10px] top-[8px] w-1.5 h-1.5 rounded-full bg-laya-orange/50"></div>

					<span class="text-[11px] font-medium text-surface-300">{chapter.label}</span>
					{#if chapter.timestamp}
						<span class="text-[10px] text-surface-600 tabular-nums ml-1.5">
							{new Date(chapter.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
						</span>
					{/if}
				</div>
				<!-- Chapter cards: nested one level deeper under the chapter -->
				{#if chapter.card_ids.length > 0}
					<div class="relative pl-5">
						<!-- Continue parent vertical line if not last chapter -->
						{#if ci < cluster.chapters.length - 1}
							<div class="absolute left-0 top-0 bottom-0 w-px bg-surface-700/40"></div>
						{/if}
						{#each chapter.card_ids as cardId, idx}
							{#if cardsById[cardId]}
								<TraceCard card={cardsById[cardId]} compact isLast={idx === chapter.card_ids.length - 1} {expandAll} />
							{/if}
						{/each}
					</div>
				{/if}
			{/each}
		</div>
	{:else}
		<!-- Flat: all cards directly -->
		<div class="relative">
			{#each cluster.timeline as card, idx}
				<TraceCard {card} compact isLast={idx === cluster.timeline.length - 1} {expandAll} />
			{/each}
		</div>
	{/if}
{:else}
	<!-- Original full timeline view -->
	<div class="relative">
		<div class="absolute left-1.5 top-0 bottom-0 w-px bg-surface-700"></div>

		{#if cluster.chapters.length > 0}
			{#each cluster.chapters as chapter, i}
				<div class="relative flex items-center gap-3 mb-4 {i > 0 ? 'mt-6' : ''}">
					<div class="w-3 h-3 rounded-full bg-laya-orange/30 border-2 border-laya-orange z-10"></div>
					<div class="flex items-center gap-2">
						<span class="text-laya-base font-semibold text-surface-200">{chapter.label}</span>
						{#if chapter.timestamp}
							<span class="text-xs text-surface-500">
								{new Date(chapter.timestamp).toLocaleDateString(undefined, {
									month: 'short',
									day: 'numeric',
									year: 'numeric'
								})}
							</span>
						{/if}
					</div>
				</div>
				<div class="space-y-2 mb-2">
					{#each chapter.card_ids as cardId}
						{#if cardsById[cardId]}
							<TraceCard card={cardsById[cardId]} />
						{/if}
					{/each}
				</div>
			{/each}
		{:else}
			<div class="space-y-2">
				{#each cluster.timeline as card}
					<TraceCard {card} />
				{/each}
			</div>
		{/if}

		{#if cluster.timeline.length > 0}
			<div class="relative flex items-center gap-3 mt-6">
				<div class="w-3 h-3 rounded-full bg-surface-600 border-2 border-surface-500 z-10"></div>
				<span class="text-xs text-surface-500 italic">
					{cluster.status_summary.current_state}
				</span>
			</div>
		{/if}
	</div>
{/if}
