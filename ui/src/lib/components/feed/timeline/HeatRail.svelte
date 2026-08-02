<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Heat rail — the day's whole event volume (not just the ~few that became
	cards) as density bars, plus attention ticks for everything that is burning.

	It is PINNED: it maps the full day onto its own height instead of scrolling
	with the lanes, which is the point — an escalation at 07:00 must stay visible
	while you are reading 16:00. The translucent band shows which slice of the day
	the lanes are currently showing, and clicking anywhere scrolls them there.
-->
<script lang="ts">
	import { formatMinutes } from '$lib/timeline/scale';
	import type { AttentionMark } from '$lib/timeline/threads';

	let {
		buckets = [],
		bucketMinutes = 30,
		marks = [],
		domainStart,
		domainEnd,
		viewport = null,
		width = 46,
		showSpace = false,
		onseek,
		onhover,
		onleave
	}: {
		buckets?: { start_minute: number; count: number }[];
		bucketMinutes?: number;
		marks?: AttentionMark[];
		domainStart: number;
		domainEnd: number;
		/** Minute range currently visible in the lanes column. */
		viewport?: { from: number; to: number } | null;
		width?: number;
		/** Only true when the day's threads span more than one space. */
		showSpace?: boolean;
		onseek: (minute: number) => void;
		onhover?: (el: HTMLElement, text: string) => void;
		onleave?: () => void;
	} = $props();

	let el: HTMLElement | undefined = $state();
	let railHeight = $state(0);

	const TOP = 18; // room for the HEAT label
	const span = $derived(Math.max(1, domainEnd - domainStart));
	const usable = $derived(Math.max(0, railHeight - TOP - 6));
	const railY = (minute: number) =>
		TOP + ((Math.min(Math.max(minute, domainStart), domainEnd) - domainStart) / span) * usable;

	const visible = $derived(buckets.filter((b) => b.start_minute + bucketMinutes > domainStart && b.start_minute < domainEnd));
	const peak = $derived(Math.max(1, ...visible.map((b) => b.count)));

	const tickColor: Record<AttentionMark['kind'], string> = {
		escalating: 'var(--tl-tick-escalate)',
		agent: 'var(--tl-tick-agent)',
		'needs-you': 'var(--tl-tick-needs-you)'
	};

	function seekFromEvent(e: MouseEvent) {
		if (!el || usable <= 0) return;
		const rect = el.getBoundingClientRect();
		const ratio = (e.clientY - rect.top - TOP) / usable;
		onseek(domainStart + Math.min(1, Math.max(0, ratio)) * span);
	}
</script>

<div
	bind:this={el}
	bind:clientHeight={railHeight}
	class="tl-glass-surface relative h-full flex-none cursor-pointer border-l"
	style="width: {width}px; border-color: var(--tl-divider); background: var(--tl-rail-bg);"
	role="slider"
	tabindex="0"
	aria-label="Day density"
	aria-valuemin={domainStart}
	aria-valuemax={domainEnd}
	aria-valuenow={viewport?.from ?? domainStart}
	onclick={seekFromEvent}
	onkeydown={(e) => {
		if (e.key === 'ArrowDown') onseek((viewport?.from ?? domainStart) + 60);
		if (e.key === 'ArrowUp') onseek((viewport?.from ?? domainStart) - 60);
	}}
>
	<span class="absolute inset-x-0 top-1.5 text-center font-mono text-[8px] uppercase tracking-[0.1em]" style="color: var(--tl-micro)">Heat</span>

	{#if viewport}
		<div
			class="pointer-events-none absolute inset-x-0"
			style="top: {railY(viewport.from)}px; height: {Math.max(2, railY(viewport.to) - railY(viewport.from))}px;
				background: color-mix(in oklch, var(--color-laya-orange) 10%, transparent);
				border-top: 1px solid color-mix(in oklch, var(--color-laya-orange) 35%, transparent);
				border-bottom: 1px solid color-mix(in oklch, var(--color-laya-orange) 35%, transparent);"
		></div>
	{/if}

	{#each visible as bucket (bucket.start_minute)}
		{@const ratio = bucket.count / peak}
		{@const top = railY(bucket.start_minute)}
		{@const height = Math.max(2, railY(bucket.start_minute + bucketMinutes) - top - 1)}
		<div
			class="absolute rounded-[2px]"
			style="top: {top}px; height: {height}px; right: 4px; width: {3 + ratio * 27}px;
				background: color-mix(in oklch, var(--tl-heat) {Math.round((0.14 + ratio * 0.45) * 100)}%, transparent);"
			role="presentation"
			onmouseenter={(e) =>
				onhover?.(
					e.currentTarget as HTMLElement,
					`${formatMinutes(bucket.start_minute)}–${formatMinutes(bucket.start_minute + bucketMinutes)} · ${bucket.count.toLocaleString()} events`
				)}
			onmouseleave={() => onleave?.()}
		></div>
	{/each}

	{#each marks as mark, i (mark.entityId + i)}
		{@const spaceColor = showSpace ? mark.spaceColor : undefined}
		<!-- Across spaces the tick's tail takes the space colour while its leading
		     4px keeps the attention KIND — which of the two you need first depends
		     on the moment, and the tick is wide enough to say both. -->
		<div
			class="absolute h-[3px] w-3 overflow-hidden rounded-[2px]"
			style="left: 2px; top: {railY(mark.minute)}px; background: {spaceColor ?? tickColor[mark.kind]};"
			role="presentation"
			onmouseenter={(e) =>
				onhover?.(
					e.currentTarget as HTMLElement,
					mark.label + (showSpace && mark.spaceName ? ` · ${mark.spaceName}` : '')
				)}
			onmouseleave={() => onleave?.()}
		>
			{#if spaceColor}
				<span class="absolute inset-y-0 left-0 w-1" style="background: {tickColor[mark.kind]};"></span>
			{/if}
		</div>
	{/each}
</div>
