<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Recent-cards drawer, carved out of feed/+page.svelte (P7-7). Presentational plus
	its own reorder FLIP: clicking an entry re-visits the card, which moves it to the
	top of the recents store; this component owns recentListEl so it captures before
	the reorder and plays the vertical FLIP itself (via the shared utils/flip helper).
	Navigation + the feed-reflow animation stay in the parent (onNavigate / onToggle).
-->
<script lang="ts">
	import { recentDrawerOpen, clearRecentCards, type RecentCardEntry } from '$lib/stores/recentCards';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { capturePositions, playFlip } from '$lib/utils/flip';

	let {
		cards,
		spaceFiltered,
		selectedCardId,
		selectedGroupEntityId,
		onNavigate,
		onToggle,
	}: {
		cards: RecentCardEntry[];
		spaceFiltered: boolean;
		selectedCardId: string | null;
		selectedGroupEntityId: string | null;
		onNavigate: (entry: RecentCardEntry) => void;
		onToggle: () => void;
	} = $props();

	let recentListEl: HTMLElement | null = $state(null);

	function formatRecentTime(epochMs: number): string {
		const diff = Date.now() - epochMs;
		const mins = Math.floor(diff / 60_000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		return `${days}d ago`;
	}

	function onEntry(entry: RecentCardEntry) {
		// Capture BEFORE onNavigate re-visits the card (which reorders the recents
		// store); playFlip awaits a tick so it measures the reordered list. Vertical,
		// 250ms. Reduced motion / no list element: just navigate.
		if ($reducedMotion || !recentListEl) {
			onNavigate(entry);
			return;
		}
		const old = capturePositions(recentListEl, '[data-recent-id]', (el) => el.dataset.recentId);
		onNavigate(entry);
		void playFlip(recentListEl, '[data-recent-id]', (el) => el.dataset.recentId, old, {
			axis: 'y',
			durationMs: 250,
		});
	}
</script>

<div class="flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out {$recentDrawerOpen ? 'w-[260px]' : 'w-0'}">
	<div class="flex h-full w-[260px] flex-col overflow-hidden rounded-xl border {$glassTheme ? 'glass-card border-surface-700/40 bg-surface-900/40' : 'border-surface-700/50 bg-surface-900/60'}">
		<div class="flex items-center justify-between border-b border-surface-700/50 px-3 py-2">
			<span class="text-laya-secondary font-medium text-surface-300">Recent Cards</span>
			<div class="flex items-center gap-1">
				{#if cards.length > 0}
					<button
						class="rounded p-0.5 text-surface-600 transition-colors hover:text-surface-300"
						onclick={() => clearRecentCards()}
						title="Clear history"
					>
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
						</svg>
					</button>
				{/if}
				<button
					class="rounded p-0.5 text-surface-600 transition-colors hover:text-surface-300"
					onclick={onToggle}
					title="Close"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>
		</div>
		<div bind:this={recentListEl} class="flex-1 overflow-y-auto">
			{#if cards.length === 0}
				<div class="flex flex-col items-center justify-center px-4 py-8 text-surface-600">
					<svg class="mb-2 h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
					</svg>
					<p class="text-laya-secondary">{spaceFiltered ? 'No recent cards in selected spaces' : 'No recent cards yet'}</p>
					<p class="mt-0.5 text-laya-micro text-surface-700">Cards you view will appear here</p>
				</div>
			{:else}
				{#each cards as entry (entry.card_id)}
					<button
						data-recent-id={entry.card_id}
						class="flex w-full flex-col gap-0.5 border-b border-surface-800/50 px-3 py-2 text-left transition-colors {$glassTheme ? 'hover:bg-white/[0.06]' : 'hover:bg-surface-800/60'}
							{(selectedCardId === entry.card_id || (entry.type === 'group' && selectedGroupEntityId === entry.card_id)) ? 'bg-laya-orange/5 border-l-2 border-l-laya-orange/40' : ''}"
						onclick={() => onEntry(entry)}
					>
						<div class="flex items-start justify-between gap-2">
							<span class="line-clamp-1 text-laya-secondary text-surface-200">{entry.header}</span>
							<span class="shrink-0 text-laya-micro text-surface-600">{formatRecentTime(entry.visited_at)}</span>
						</div>
						<span class="line-clamp-1 text-laya-micro text-surface-500">
							{#if entry.type === 'group'}
								{entry.card_count} cards{#if entry.source_ref} · {entry.source_ref}{/if}
							{:else}
								{#if entry.source_ref}{entry.source_ref}{:else if entry.entity_id}{entry.entity_id}{:else if entry.category}{entry.category}{/if}
							{/if}
							{#if entry.space_name}
								<span class="text-surface-600"> · {entry.space_name}</span>
							{/if}
						</span>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</div>
