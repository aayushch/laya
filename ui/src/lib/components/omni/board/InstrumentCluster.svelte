<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniItem, OmniSnapshot, OmniVolumeResponse } from '$lib/api/types';
	import AttentionLoad from './AttentionLoad.svelte';
	import EventVolume from './EventVolume.svelte';
	import PlatformMix from './PlatformMix.svelte';
	import CompressionGauge from './CompressionGauge.svelte';

	let {
		snapshot,
		volume,
		attentionItems,
		attentionDelta,
		nextSynthesisAt,
		resynthesizing
	}: {
		snapshot: OmniSnapshot;
		volume: OmniVolumeResponse | null;
		attentionItems: OmniItem[];
		attentionDelta: number | null;
		nextSynthesisAt: string | null;
		resynthesizing: boolean;
	} = $props();

	// "N lines" is the whole board's item count — the other half of the
	// compression claim, and the number the ratio is a ratio *of*.
	const lineCount = $derived(
		snapshot.sections.reduce((sum, s) => sum + s.items.length, 0)
	);

	// The mix instrument prefers the raw event counts from /omni/volume (which
	// include filtered events that never produced a card). When that call hasn't
	// landed, fall back to summing the per-item live platform counts so the
	// instrument shows something true rather than sitting empty.
	const platforms = $derived.by(() => {
		if (volume && Object.keys(volume.platforms).length > 0) return volume.platforms;
		const counts: Record<string, number> = {};
		for (const section of snapshot.sections) {
			for (const item of section.items) {
				for (const [p, n] of Object.entries(item.live?.platform_counts ?? {})) {
					counts[p] = (counts[p] ?? 0) + n;
				}
			}
		}
		return counts;
	});
</script>

<!-- min-height, not auto: the volume instrument sizes its bars with flex-1, which
     collapses to nothing if the row's height is left to the tallest sibling and
     that sibling happens to be short (an empty attention instrument). -->
<div
	class="om-bar om-glass flex flex-none gap-2.5 px-[18px] pb-3"
	style="min-height: calc(108px * var(--om-scale));"
>
	<AttentionLoad items={attentionItems} delta={attentionDelta} />
	<EventVolume {volume} />
	<PlatformMix {platforms} />
	<CompressionGauge
		ratio={snapshot.stats?.compression_ratio ?? 0}
		eventsProcessed={volume?.total ?? snapshot.stats?.events_processed ?? 0}
		{lineCount}
		{nextSynthesisAt}
		{resynthesizing}
	/>
</div>
