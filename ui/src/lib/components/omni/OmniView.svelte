<script lang="ts">
	import type { OmniSnapshot, OmniItem as OmniItemType } from '$lib/api/types';
	import OmniItem from './OmniItem.svelte';

	let { snapshot, onPin, onUnpin, onDrillDown, onBookmark }: {
		snapshot: OmniSnapshot;
		onPin?: (item: OmniItemType) => void;
		onUnpin?: (item: OmniItemType) => void;
		onDrillDown?: (cardIds: string[]) => void;
		onBookmark?: (item: OmniItemType) => void;
	} = $props();

	const sectionMeta: Record<string, { title: string; icon: string; emptyText: string }> = {
		attention: {
			title: 'Needs Attention',
			icon: '!',
			emptyText: 'Nothing urgent right now.'
		},
		recent: {
			title: 'Recent Activity',
			icon: '',
			emptyText: 'No recent activity.'
		},
		period: {
			title: 'This Week',
			icon: '',
			emptyText: 'No weekly summary yet.'
		},
		milestone: {
			title: 'Milestones',
			icon: '',
			emptyText: 'No milestones recorded.'
		}
	};

	// Order sections: attention → recent → period → milestone
	const sectionOrder = ['attention', 'recent', 'period', 'milestone'];

	const orderedSections = $derived(
		sectionOrder
			.map((type) => snapshot.sections.find((s) => s.type === type))
			.filter((s): s is NonNullable<typeof s> => s !== undefined)
	);
</script>

<div class="flex flex-col gap-4">
	{#each orderedSections as section}
		{@const meta = sectionMeta[section.type] || { title: section.type, icon: '', emptyText: '' }}
		{@const genericLabels = ['period', 'this period', 'period (this week)', 'recent', 'attention', 'milestone', 'milestones']}
		{@const hasCustomLabel = section.label && !genericLabels.includes(section.label.toLowerCase().trim())}
		<div class="rounded-xl border border-surface-700/50 bg-surface-900/50 p-4">
			<!-- Section header -->
			<div class="mb-3 flex items-center gap-2">
				{#if section.type === 'attention'}
					<span class="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/20 text-[10px] font-bold text-red-400">!</span>
				{:else if section.type === 'recent'}
					<span class="flex h-5 w-5 items-center justify-center rounded-full bg-laya-orange/20 text-[10px] text-laya-orange">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
					</span>
				{:else if section.type === 'period'}
					<span class="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500/20 text-[10px] text-blue-400">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
					</span>
				{:else}
					<span class="flex h-5 w-5 items-center justify-center rounded-full bg-laya-gold/20 text-[10px] text-laya-gold">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
					</span>
				{/if}
				<h3 class="text-sm font-semibold text-surface-200">
					{meta.title}{#if hasCustomLabel} <span class="font-normal text-surface-400">({section.label})</span>{/if}
				</h3>
				{#if section.items.length > 0}
					<span class="rounded-full bg-surface-800 px-1.5 py-0.5 text-[10px] text-surface-400">
						{section.items.length}
					</span>
				{/if}
			</div>

			<!-- Items -->
			{#if section.items.length === 0}
				<p class="px-3 py-2 text-xs text-surface-500 italic">{meta.emptyText}</p>
			{:else}
				{@const totalSourceCards = section.items.reduce((sum, i) => sum + i.source_cards.length, 0)}
				<div class="flex flex-col gap-0.5">
					{#each section.items as item}
						<OmniItem {item} {onPin} {onUnpin} {onDrillDown} {onBookmark} />
					{/each}
				</div>
				<!-- Condensed indicator — shows when items aggregate more events than shown -->
				{#if totalSourceCards > section.items.length}
					<p class="mt-2 px-3 text-[10px] text-surface-500">
						Synthesized from {totalSourceCards} events
					</p>
				{/if}
			{/if}
		</div>
	{/each}
</div>
