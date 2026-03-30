<script lang="ts">
	import type { TraceCluster, ActionCard } from '$lib/api/types';
	import TraceCard from './TraceCard.svelte';

	let {
		cluster
	}: {
		cluster: TraceCluster;
	} = $props();

	// Build a map of card_id -> card for fast lookup
	let cardsById = $derived(
		Object.fromEntries(cluster.timeline.map((c) => [c.card_id, c]))
	);
</script>

<div class="relative">
	<!-- Vertical timeline line -->
	<div class="absolute left-1.5 top-0 bottom-0 w-px bg-surface-700"></div>

	{#if cluster.chapters.length > 0}
		{#each cluster.chapters as chapter, i}
			<!-- Chapter separator -->
			<div class="relative flex items-center gap-3 mb-4 {i > 0 ? 'mt-6' : ''}">
				<div class="w-3 h-3 rounded-full bg-laya-orange/30 border-2 border-laya-orange z-10"></div>
				<div class="flex items-center gap-2">
					<span class="text-sm font-semibold text-surface-200">{chapter.label}</span>
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

			<!-- Cards in this chapter -->
			<div class="space-y-2 mb-2">
				{#each chapter.card_ids as cardId}
					{#if cardsById[cardId]}
						<TraceCard card={cardsById[cardId]} />
					{/if}
				{/each}
			</div>
		{/each}
	{:else}
		<!-- No chapters — just render all cards -->
		<div class="space-y-2">
			{#each cluster.timeline as card}
				<TraceCard {card} />
			{/each}
		</div>
	{/if}

	<!-- Timeline end marker -->
	{#if cluster.timeline.length > 0}
		<div class="relative flex items-center gap-3 mt-6">
			<div class="w-3 h-3 rounded-full bg-surface-600 border-2 border-surface-500 z-10"></div>
			<span class="text-xs text-surface-500 italic">
				{cluster.status_summary.current_state}
			</span>
		</div>
	{/if}
</div>
