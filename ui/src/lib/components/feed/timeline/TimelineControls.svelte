<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Timeline control strip: SOURCE chips (which write the SHARED platform filter,
	so switching to card/list keeps the same selection), the zoom stepper, and
	jump-to-now. Persona/space/status filters deliberately stay in FilterPopover
	rather than being duplicated here.
-->
<script lang="ts">
	import { feedFilters } from '$lib/stores/feedFilters';
	import { timelineView, zoomLabel } from '$lib/stores/timelineView';
	import { platformDotColor, platformLabel } from '$lib/utils/cardVisuals';
	import { formatMinutes } from '$lib/timeline/scale';

	let {
		sourcePlatforms = [],
		isToday = false,
		nowMinute = 0,
		hasMore = false,
		loadingMore = false,
		remaining = 0,
		onloadmore,
		onjumptonow
	}: {
		/** Platforms present on the day, with their event counts. */
		sourcePlatforms?: { key: string; count: number }[];
		isToday?: boolean;
		nowMinute?: number;
		/** The feed pages groups; the timeline says so rather than silently truncating. */
		hasMore?: boolean;
		loadingMore?: boolean;
		remaining?: number;
		onloadmore?: () => void;
		onjumptonow: () => void;
	} = $props();

	// Empty filter means "everything", so every chip reads as active.
	const included = $derived(new Set($feedFilters.platformFilters));
	const isActive = (key: string) => included.size === 0 || included.has(key);

	function toggle(key: string) {
		const all = sourcePlatforms.map((p) => p.key);
		let next: string[];
		if (included.size === 0) {
			// First click switches one source OFF rather than soloing it — the chips
			// start lit, so a click has to read as "turn this one off".
			next = all.filter((k) => k !== key);
		} else if (included.has(key)) {
			next = [...included].filter((k) => k !== key);
		} else {
			next = [...included, key];
		}
		// Back to "all present sources selected" is the same thing as no filter.
		const coversAll = all.length > 0 && all.every((k) => next.includes(k));
		$feedFilters.platformFilters = coversAll ? [] : next;
	}
</script>

<div
	class="tl-glass-surface flex h-[41px] flex-none items-center gap-1.5 overflow-hidden border-t px-3.5"
	style="border-color: var(--tl-divider); background: var(--tl-controls-bg);"
>
	<span class="shrink-0 font-mono text-[9px] uppercase tracking-[0.12em]" style="color: var(--tl-quiet-label)">Sources</span>

	<div class="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto no-scrollbar">
		{#each sourcePlatforms as source (source.key)}
			{@const active = isActive(source.key)}
			<button
				class="inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[10px] font-medium transition-colors"
				style={active
					? 'background: color-mix(in oklch, var(--color-laya-orange) 16%, transparent); border-color: color-mix(in oklch, var(--color-laya-orange) 35%, transparent); color: var(--color-laya-orange);'
					: 'background: var(--tl-chip-inactive); border-color: var(--tl-chip-inactive-border); color: var(--color-surface-400);'}
				onclick={() => toggle(source.key)}
				title="{active ? 'Hide' : 'Show'} {platformLabel(source.key)}"
			>
				<span class="h-1.5 w-1.5 shrink-0 rounded-full" style="background-color: {platformDotColor(source.key)}; opacity: {active ? 1 : 0.5}"></span>
				{platformLabel(source.key)}
				<span class="font-mono opacity-65">{source.count.toLocaleString()}</span>
			</button>
		{/each}
		{#if sourcePlatforms.length === 0}
			<span class="text-[10px] text-surface-500">No events on this day</span>
		{/if}
	</div>

	{#if hasMore}
		<button
			class="ml-2 shrink-0 rounded-full border px-2.5 py-[3px] text-[10px] font-medium transition-colors disabled:opacity-50"
			style="border-color: var(--tl-control-border); background: var(--tl-control-bg); color: var(--color-surface-300);"
			onclick={() => onloadmore?.()}
			disabled={loadingMore}
			title="Only the first page of threads is loaded"
		>
			{loadingMore ? 'Loading…' : `+${remaining.toLocaleString()} more threads`}
		</button>
	{/if}

	<!-- Zoom -->
	<span class="ml-2 shrink-0 font-mono text-[9px] uppercase tracking-[0.12em]" style="color: var(--tl-quiet-label)">Zoom</span>
	<div
		class="flex shrink-0 items-center overflow-hidden rounded-[7px] border"
		style="border-color: var(--tl-control-border); background: var(--tl-control-bg);"
	>
		<button
			class="px-2 py-0.5 text-surface-400 transition-colors hover:text-surface-100 disabled:opacity-40"
			onclick={() => timelineView.zoomBy(-1)}
			disabled={$timelineView.hourPx === 15}
			aria-label="Zoom out"
		>−</button>
		<span class="w-9 border-x py-0.5 text-center font-mono text-[10px] text-surface-200" style="border-color: var(--tl-chip-inactive-border)">
			{zoomLabel($timelineView.hourPx)}
		</span>
		<button
			class="px-2 py-0.5 text-surface-400 transition-colors hover:text-surface-100 disabled:opacity-40"
			onclick={() => timelineView.zoomBy(1)}
			disabled={$timelineView.hourPx === 120}
			aria-label="Zoom in"
		>+</button>
	</div>

	<!-- Jump to now — disabled rather than hidden on other days, so the strip
	     never changes width as you page through the date nav. -->
	<button
		class="ml-1.5 inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[10px] font-semibold transition-colors disabled:opacity-40"
		style="border-color: color-mix(in oklch, var(--color-laya-orange) 40%, transparent); background: color-mix(in oklch, var(--color-laya-orange) 14%, transparent); color: var(--color-laya-orange);"
		onclick={onjumptonow}
		disabled={!isToday}
		title={isToday ? 'Scroll the day to the current time' : 'Only available on today'}
	>
		<span class="h-1.5 w-1.5 rounded-full" style="background: var(--tl-now)"></span>
		Jump to now
		{#if isToday}
			<span class="font-mono">· {formatMinutes(nowMinute)}</span>
		{/if}
	</button>
</div>

<style>
	.no-scrollbar { scrollbar-width: none; }
	.no-scrollbar::-webkit-scrollbar { display: none; }
</style>
