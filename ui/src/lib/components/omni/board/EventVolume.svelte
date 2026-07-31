<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniVolumeResponse } from '$lib/api/types';
	import { num } from '$lib/omni/layers';

	let { volume }: { volume: OmniVolumeResponse | null } = $props();

	const series = $derived(volume?.series ?? []);
	// Scale to the busiest day so a quiet fortnight still shows relief. Floor of
	// 1 keeps the division safe when every day is empty.
	const peak = $derived(Math.max(1, ...series.map((d) => d.count)));

	function label(date: string): string {
		const d = new Date(date + 'T00:00:00');
		return d
			.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
			.toUpperCase();
	}

	// Three ticks — start, middle, end — matching the axis in the design.
	const axis = $derived.by(() => {
		if (series.length === 0) return [];
		const mid = Math.floor(series.length / 2);
		return [series[0], series[mid], series[series.length - 1]].map((d) => label(d.date));
	});
</script>

<div class="om-instrument om-glass flex min-w-0 flex-1 flex-col gap-[7px] rounded-[9px] px-[13px] py-[11px]">
	<div class="flex items-center gap-2">
		<span class="om-micro whitespace-nowrap">Event volume · {volume?.days ?? 14} days</span>
		<span class="flex-1"></span>
		<span class="om-mono text-[calc(11px*var(--om-scale))]" style="color: var(--om-text-strong);">
			{num(volume?.total)}
		</span>
		<span class="text-[calc(9.5px*var(--om-scale))]" style="color: var(--om-text-meta);">
			today {num(volume?.today)}
		</span>
	</div>

	<div class="relative min-h-0 flex-1">
		<div class="absolute inset-0 flex items-end gap-[3px]">
			{#each series as day (day.date)}
				{@const isToday = day.date === volume?.today_date}
				<div
					class="flex-1 rounded-t-[2px]"
					style="height: {Math.max(day.count > 0 ? 6 : 2, (day.count / peak) * 100)}%;
						background: {isToday ? 'var(--om-volume-today)' : 'var(--om-volume-bar)'};"
					title="{label(day.date)} — {num(day.count)} events"
				></div>
			{/each}
		</div>
	</div>

	<div
		class="om-mono flex justify-between text-[calc(8px*var(--om-scale))]"
		style="color: var(--om-text-faint);"
	>
		{#each axis as tick}<span>{tick}</span>{/each}
	</div>
</div>
