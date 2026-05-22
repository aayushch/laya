<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniItem } from '$lib/api/types';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import { glassTheme } from '$lib/stores/glassTheme';

	let { item, onPin, onUnpin, onDrillDown, onBookmark }: {
		item: OmniItem;
		onPin?: (item: OmniItem) => void;
		onUnpin?: (item: OmniItem) => void;
		onDrillDown?: (cardIds: string[]) => void;
		onBookmark?: (item: OmniItem) => void;
	} = $props();

	const priorityColors: Record<string, string> = {
		CRITICAL: 'text-red-400',
		HIGH: 'text-orange-400',
		MEDIUM: 'text-surface-300',
		LOW: 'text-laya-amber'
	};

	const hasSourceCards = $derived(item.source_cards.length > 0);

	function handleRowClick() {
		if (hasSourceCards) onDrillDown?.(item.source_cards);
	}
</script>

<!--
	The whole row is the click target now — clicking it opens the cards synthesised
	into this Omni item. The "from N events" footnote stays as plain metadata so
	the row's affordance isn't ambiguous. The element renders as a real <button>
	when there are source cards (implicit role + native focus/keyboard) and a plain
	<div> otherwise (no interactivity). svelte's static analysis can't follow the
	dynamic element so the ignore directive is required.
-->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<svelte:element
	this={hasSourceCards ? 'button' : 'div'}
	type={hasSourceCards ? 'button' : undefined}
	class="group flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors {hasSourceCards ? 'cursor-pointer' : ''} {$glassTheme ? 'glass-hover' : 'list-row-hover'}"
	data-omni-item={item.source_cards[0] ?? ''}
	onclick={hasSourceCards ? handleRowClick : undefined}
>
	<!-- Priority dot -->
	<span class="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full {item.priority === 'CRITICAL' ? 'bg-red-400' : item.priority === 'HIGH' ? 'bg-orange-400' : item.priority === 'LOW' ? 'bg-laya-amber' : 'bg-surface-500'}"></span>

	<!-- Content -->
	<div class="flex-1 min-w-0">
		<p class="text-laya-base leading-relaxed {priorityColors[item.priority] || 'text-surface-300'}">
			{item.text}
		</p>

		<!-- Platform badges + card links -->
		<div class="mt-1 flex items-center gap-1.5 flex-wrap">
			{#each item.platforms as platform}
				<PlatformBadge {platform} />
			{/each}

			{#if item.source_cards.length > 0}
				<span class="text-[10px] text-surface-500">
					{item.source_cards.length === 1 ? 'from 1 event' : `from ${item.source_cards.length} events`}
				</span>
			{/if}
		</div>
	</div>

	<!-- Bookmark & Pin disabled: with delta-based snapshots the reconciliation
	     complexity outweighs the value. Backend endpoints remain intact so these
	     can be re-enabled by uncommenting this block.
	<!--
	<div class="mt-1 flex items-center gap-1 flex-shrink-0">
		<div class="group/bookmark relative transition-opacity
			{item.bookmarked ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
			<button
				onclick={() => onBookmark?.(item)}
				class="text-surface-500 hover:text-laya-orange {item.bookmarked ? 'text-laya-orange' : ''}"
				aria-label={item.bookmarked ? 'Remove bookmark' : 'Bookmark'}
			>
				<svg class="h-4.5 w-4.5" viewBox="0 0 24 24" fill={item.bookmarked ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
					<path d="M5 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16l-7-3.5L5 21V5z" />
				</svg>
			</button>
			<span class="pointer-events-none absolute right-0 top-full z-50 mt-1 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/bookmark:opacity-100">
				{item.bookmarked ? 'Remove bookmark' : 'Bookmark'}
			</span>
		</div>

		<div class="group/tip relative transition-opacity
			{item.pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
			<button
				onclick={() => item.pinned ? onUnpin?.(item) : onPin?.(item)}
				class="text-surface-500 hover:text-laya-orange {item.pinned ? 'text-laya-orange' : ''}"
				aria-label={item.pinned ? 'Unpin item' : 'Pin to preserve'}
			>
				<svg class="h-5 w-5" viewBox="0 0 24 24" fill={item.pinned ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
					<path d="M9 4v6l-2 4h4v6l1 2 1-2v-6h4l-2-4V4" />
					<line x1="8" y1="4" x2="16" y2="4" />
				</svg>
			</button>
			<span class="pointer-events-none absolute right-0 top-full z-50 mt-1 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">
				{item.pinned ? 'Unpin' : 'Pin to preserve'}
			</span>
		</div>
	</div>
	-->
</svelte:element>
