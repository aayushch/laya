<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Day-summary modal, carved out of feed/+page.svelte (P7-7). Presentational only:
	the parent owns the summary data + open state and passes them in; this component
	just renders the overlay and forwards close / goto-card intents.
-->
<script lang="ts">
	import type { DaySummary } from '$lib/api/types';
	import DaySummaryComponent from '$lib/components/feed/DaySummary.svelte';
	import { glassTheme } from '$lib/stores/glassTheme';

	let {
		open,
		summary,
		loading,
		updatedAt,
		dateLabel,
		spaceFilter,
		onClose,
		onGotoCard,
	}: {
		open: boolean;
		summary: DaySummary | null;
		loading: boolean;
		updatedAt: string | null;
		dateLabel: string;
		spaceFilter: string[];
		onClose: () => void;
		onGotoCard: (cardId: string) => void;
	} = $props();
</script>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
		onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
	>
		<div class="relative mx-4 flex max-h-[90vh] w-full max-w-6xl flex-col rounded-xl border {$glassTheme ? 'glass-card border-surface-700/40 bg-surface-900/40' : 'border-surface-700 bg-surface-800 shadow-2xl'}">
			<!-- Header -->
			<div class="flex items-center justify-between border-b px-6 py-4 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
				<div class="flex items-center gap-2">
					<svg class="h-4 w-4 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
					</svg>
					<h2 class="text-laya-base font-semibold text-surface-100">Day Summary — {dateLabel}</h2>
				</div>
				<button
					onclick={onClose}
					class="text-surface-500 hover:text-surface-300 transition-colors"
					aria-label="Close"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>
			<!-- Body -->
			<div class="flex-1 overflow-y-auto p-6">
				{#if loading}
					<div class="flex h-48 items-center justify-center text-surface-400">
						<span class="text-laya-base">Loading summary...</span>
					</div>
				{:else}
					<DaySummaryComponent summary={summary} updatedAt={updatedAt} ongotocard={onGotoCard} spaceFilter={spaceFilter} />
				{/if}
			</div>
		</div>
	</div>
{/if}
