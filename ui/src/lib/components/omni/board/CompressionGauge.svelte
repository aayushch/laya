<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { countdownTo, num } from '$lib/omni/layers';

	let {
		ratio,
		eventsProcessed,
		lineCount,
		nextSynthesisAt,
		resynthesizing
	}: {
		/** OmniStats.compression_ratio — 0..1. */
		ratio: number;
		eventsProcessed: number;
		/** Total items standing across all four sections. */
		lineCount: number;
		nextSynthesisAt: string | null;
		resynthesizing: boolean;
	} = $props();

	const pct = $derived(Math.round(Math.max(0, Math.min(1, ratio || 0)) * 100));

	// Recomputed on a timer so the countdown doesn't sit frozen on a page that
	// stays open for hours. One minute is finer than the value's own resolution.
	let now = $state(Date.now());
	$effect(() => {
		const id = setInterval(() => (now = Date.now()), 60_000);
		return () => clearInterval(id);
	});
	const countdown = $derived(countdownTo(nextSynthesisAt, now));
</script>

<div
	class="om-glass flex w-[204px] flex-none flex-col gap-1 rounded-[9px] px-[11px]"
	style="border: 1px solid var(--om-comp-border); background: var(--om-comp-bg);
		padding-block: calc(8px * var(--om-density));"
>
	<span class="om-micro" style="color: var(--om-comp-label);">Compression</span>

	<div class="flex items-baseline gap-[7px]">
		<span class="om-num-lg" style="color: var(--om-comp-num);">
			{pct}<span class="text-[calc(18px*var(--om-scale))]">%</span>
		</span>
		<span class="om-pill-t" style="color: var(--om-text-dim);">distilled</span>
	</div>

	<div class="om-mono text-[calc(10px*var(--om-scale))]" style="color: var(--om-text-meta);">
		{num(eventsProcessed)} events → {num(lineCount)} {lineCount === 1 ? 'line' : 'lines'}
	</div>

	<div class="flex-1"></div>

	<div class="om-hint" style="color: var(--om-text-meta);">
		{#if resynthesizing}
			<span class="om-mono" style="color: var(--om-comp-num);">Synthesizing now…</span>
		{:else if countdown}
			Next synthesis <span class="om-mono" style="color: var(--om-comp-num);">{countdown}</span>
		{:else}
			<!-- No schedule means Omni's automatic triggers are off; the only way
			     forward is the Resynthesize button, so say that rather than nothing. -->
			Manual synthesis only
		{/if}
	</div>
</div>
