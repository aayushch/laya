<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniItem } from '$lib/api/types';
	import { livePriority, num } from '$lib/omni/layers';

	let {
		items,
		delta
	}: {
		/** Items in the attention section of the displayed snapshot. */
		items: OmniItem[];
		/** Change in open-item count vs. the last version the user saw. */
		delta: number | null;
	} = $props();

	const openCount = $derived(items.length);
	const eventCount = $derived(items.reduce((sum, i) => sum + i.source_cards.length, 0));

	// Split by LIVE priority, not the frozen `item.priority`: a subject that has
	// since been merged shouldn't still be counted as a HIGH that needs you.
	const split = $derived.by(() => {
		const counts = { high: 0, medium: 0, low: 0 };
		for (const item of items) {
			const p = livePriority(item).toUpperCase();
			if (p === 'CRITICAL' || p === 'HIGH') counts.high += 1;
			else if (p === 'LOW') counts.low += 1;
			else counts.medium += 1;
		}
		return counts;
	});
	const splitTotal = $derived(split.high + split.medium + split.low);
</script>

<div
	class="om-glass flex w-[236px] flex-none flex-col gap-1.5 rounded-[9px] px-[11px]"
	style="border: 1px solid var(--om-attn-border); background: var(--om-attn-bg);
		padding-block: calc(8px * var(--om-density));"
>
	<div class="flex items-center gap-1.5">
		<span
			class="om-pulse h-1.5 w-1.5 rounded-full"
			style="background: var(--om-layer-attention);"
		></span>
		<span class="om-micro" style="color: var(--om-attn-label);">Attention load</span>
	</div>

	<div class="flex items-baseline gap-2">
		<span class="om-num-lg" style="color: var(--om-attn-num);">{openCount}</span>
		<span class="om-pill-t leading-[1.35]" style="color: var(--om-text-dim);">
			{openCount === 1 ? 'open item' : 'open items'}<br />from {num(eventCount)}
			{eventCount === 1 ? 'event' : 'events'}
		</span>
		<span class="flex-1"></span>
		{#if delta !== null && delta !== 0}
			<span
				class="om-mono text-[calc(10px*var(--om-scale))]"
				style="color: var(--om-attn-delta);"
				title="Change since the last version you looked at"
			>{delta > 0 ? `+${delta}` : delta}</span>
		{/if}
	</div>

	{#if splitTotal > 0}
		<div class="flex h-[7px] gap-[1.5px] overflow-hidden rounded">
			{#if split.high > 0}
				<span style="flex: {split.high}; background: var(--om-bar-high);"></span>
			{/if}
			{#if split.medium > 0}
				<span style="flex: {split.medium}; background: var(--om-bar-medium);"></span>
			{/if}
			{#if split.low > 0}
				<span style="flex: {split.low}; background: var(--om-bar-low);"></span>
			{/if}
		</div>
		<div
			class="flex gap-[11px] text-[calc(9.5px*var(--om-scale))]"
			style="color: var(--om-text-meta);"
		>
			<span><span class="font-semibold" style="color: var(--om-pri-high-fg);">{split.high}</span> high</span>
			<span><span class="font-semibold" style="color: var(--om-pri-medium-fg);">{split.medium}</span> medium</span>
			<span><span class="font-semibold" style="color: var(--om-text-dim);">{split.low}</span> low</span>
		</div>
	{:else}
		<!-- Nothing open is a result, not an empty state — say so rather than
		     leaving a blank instrument that reads as "not loaded yet". -->
		<div class="om-pill-t mt-auto" style="color: var(--om-text-meta);">
			Nothing needs you right now.
		</div>
	{/if}
</div>
