<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { platformDotColor, platformLabel } from '$lib/utils/cardVisuals';
	import { num } from '$lib/omni/layers';

	let { platforms }: { platforms: Record<string, number> } = $props();

	// This instrument is the visual form of Omni's cross-platform claim — the one
	// thing the old page reduced to three grey chips per item. Showing the mix
	// once, proportionally, says more than repeating chips on every row.
	const entries = $derived(
		Object.entries(platforms ?? {})
			.filter(([, n]) => n > 0)
			.sort((a, b) => b[1] - a[1])
	);
	const total = $derived(entries.reduce((sum, [, n]) => sum + n, 0));
</script>

<div class="om-instrument om-glass flex w-[300px] flex-none flex-col gap-2 rounded-[9px] px-[13px] py-[11px]">
	<span class="om-micro">Platform mix</span>

	{#if entries.length === 0}
		<span class="om-pill-t" style="color: var(--om-text-meta);">No events in this window.</span>
	{:else}
		<div class="flex h-2 gap-[1.5px] overflow-hidden rounded">
			{#each entries as [platform, count] (platform)}
				<span
					style="flex: {count}; background: {platformDotColor(platform)};"
					title="{platformLabel(platform)} — {num(count)}"
				></span>
			{/each}
		</div>

		<div class="flex flex-wrap gap-x-[10px] gap-y-1 overflow-hidden">
			{#each entries as [platform, count] (platform)}
				<span
					class="inline-flex items-center gap-1 text-[calc(9.5px*var(--om-scale))]"
					style="color: var(--om-text-dim);"
				>
					<span
						class="h-[5px] w-[5px] shrink-0 rounded-full"
						style="background: {platformDotColor(platform)};"
					></span>
					{platformLabel(platform)}
					<span class="om-mono" style="color: var(--om-text-body);">{num(count)}</span>
				</span>
			{/each}
		</div>
		{#if total > 0}
			<span class="sr-only">{num(total)} events across {entries.length} platforms</span>
		{/if}
	{/if}
</div>
