<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import type { ThroughputBucket } from '$lib/api/types';
	import { monotonePath, niceStep, formatBucketLabel } from './chartUtils';

	let { buckets, title = 'Throughput', windowMinutes = 60 }: { buckets: ThroughputBucket[]; title?: string; windowMinutes?: number } = $props();

	const COLOR_PROCESSED = 'var(--color-laya-gold)';
	const COLOR_FAILED = 'var(--color-laya-coral)';
	const COLOR_INGESTED = 'var(--color-laya-orange)';

	const padL = 38;
	const padR = 12;
	const padT = 12;
	const padB = 24;
	const W = 800;
	const H = 180;
	const chartW = W - padL - padR;
	const chartH = H - padT - padB;
	const baseline = padT + chartH;

	const rawMax = $derived(Math.max(...buckets.map((b) => b.ingested), 1));
	const step = $derived(Math.max(1, Math.round(niceStep(rawMax, 4))));
	const maxY = $derived(Math.ceil(rawMax / step) * step || step);
	const labelEvery = $derived(Math.max(1, Math.ceil(buckets.length / 10)));
	const slotW = $derived(chartW / buckets.length);

	const yTicks = $derived.by(() => {
		const ticks: number[] = [];
		for (let v = 0; v <= maxY; v += step) ticks.push(v);
		return ticks;
	});

	function xMid(i: number): number {
		return padL + i * slotW + slotW / 2;
	}

	function y(v: number): number {
		return padT + chartH - (v / maxY) * chartH;
	}

	function pt(i: number, v: number): string {
		return `${xMid(i).toFixed(1)},${y(v).toFixed(1)}`;
	}

	// Smooth ingested line (bezier — the most visible stroke)
	const ingestedPts = $derived(buckets.map((b, i) => ({ x: xMid(i), y: y(b.ingested) })));
	const ingestedLine = $derived(monotonePath(ingestedPts));

	// Stacked area polygons (60+ vertices per side — visually smooth)
	const processedPoly = $derived.by(() => {
		if (buckets.length < 2) return '';
		const curve = buckets.map((b, i) => pt(i, b.processed));
		const base = `${xMid(buckets.length - 1).toFixed(1)},${baseline.toFixed(1)} ${xMid(0).toFixed(1)},${baseline.toFixed(1)}`;
		return `${curve.join(' ')} ${base}`;
	});

	const failedBandPoly = $derived.by(() => {
		if (buckets.length < 2 || !buckets.some((b) => b.failed > 0)) return '';
		const top = buckets.map((b, i) => pt(i, b.processed + b.failed));
		const btm: string[] = [];
		for (let i = buckets.length - 1; i >= 0; i--) btm.push(pt(i, buckets[i].processed));
		return [...top, ...btm].join(' ');
	});

	let hoverIdx = $state<number | null>(null);
	let tooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showTooltip(e: MouseEvent, b: ThroughputBucket, i: number) {
		const el = e.currentTarget as SVGElement;
		const rect = el.getBoundingClientRect();
		hoverIdx = i;
		tooltip = {
			text: `${formatBucketLabel(b.minute, windowMinutes)} — Ingested: ${b.ingested}, Processed: ${b.processed}, Failed: ${b.failed}`,
			top: rect.top - 34,
			left: rect.left + rect.width / 2
		};
	}

	function hideTooltip() {
		hoverIdx = null;
		tooltip = null;
	}
</script>

<div class="rounded-xl border p-5 {$glassTheme ? 'glass-section' : 'border-surface-700 bg-surface-800'}">
	{#if title}
		<div class="mb-3 flex items-center justify-between">
			<h3 class="text-[11px] font-semibold uppercase tracking-wider text-surface-400">{title}</h3>
			<div class="flex items-center gap-4 text-[10px] text-surface-500">
				<span class="flex items-center gap-1.5">
					<span class="inline-block h-[3px] w-3 rounded-full" style="background: {COLOR_INGESTED}"></span>
					Ingested
				</span>
				<span class="flex items-center gap-1.5">
					<span class="inline-block h-2.5 w-2.5 rounded-sm opacity-60" style="background: {COLOR_PROCESSED}"></span>
					Processed
				</span>
				<span class="flex items-center gap-1.5">
					<span class="inline-block h-2.5 w-2.5 rounded-sm opacity-60" style="background: {COLOR_FAILED}"></span>
					Failed
				</span>
			</div>
		</div>
	{/if}

	{#if buckets.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<svg viewBox="0 0 {W} {H}" class="w-full" preserveAspectRatio="xMidYMid meet">
			<!-- Grid lines (skip baseline) -->
			{#each yTicks as tick}
				{#if tick > 0}
					<line
						x1={padL} y1={y(tick)} x2={W - padR} y2={y(tick)}
						stroke="currentColor" class="text-surface-700/30" stroke-width="0.5"
					/>
				{/if}
				<text
					x={padL - 7} y={y(tick) + 3}
					text-anchor="end" class="fill-surface-500"
					font-size="8" font-family="system-ui, sans-serif"
				>{tick}</text>
			{/each}

			<!-- Processed area (gold) -->
			{#if processedPoly}
				<polygon points={processedPoly} fill={COLOR_PROCESSED} opacity="0.18" />
			{/if}

			<!-- Failed band (coral, stacked on processed) -->
			{#if failedBandPoly}
				<polygon points={failedBandPoly} fill={COLOR_FAILED} opacity="0.25" />
			{/if}

			<!-- Ingested smooth line -->
			{#if ingestedLine}
				<path
					d={ingestedLine}
					fill="none" stroke={COLOR_INGESTED}
					stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
					opacity="0.85"
				/>
			{/if}

			<!-- X-axis baseline -->
			<line
				x1={padL} y1={baseline} x2={W - padR} y2={baseline}
				stroke="currentColor" class="text-surface-600" stroke-width="0.75"
			/>

			<!-- Hover crosshair + dot -->
			{#if hoverIdx !== null}
				{@const b = buckets[hoverIdx]}
				<line
					x1={xMid(hoverIdx)} y1={padT} x2={xMid(hoverIdx)} y2={baseline}
					stroke="currentColor" class="text-surface-500/50"
					stroke-width="0.5" stroke-dasharray="3 2"
				/>
				<circle cx={xMid(hoverIdx)} cy={y(b.ingested)} r="3" fill={COLOR_INGESTED} />
				{#if b.processed > 0}
					<circle cx={xMid(hoverIdx)} cy={y(b.processed)} r="2" fill={COLOR_PROCESSED} opacity="0.7" />
				{/if}
			{/if}

			<!-- X-axis labels -->
			{#each buckets as b, i}
				{#if i % labelEvery === 0}
					<text
						x={xMid(i)} y={baseline + 14}
						text-anchor="middle" class="fill-surface-500"
						font-size="8" font-family="system-ui, sans-serif"
					>{formatBucketLabel(b.minute, windowMinutes)}</text>
				{/if}
			{/each}

			<!-- Hover rects -->
			{#each buckets as b, i}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<rect
					x={padL + i * slotW} y={padT} width={slotW} height={chartH}
					fill="transparent"
					onmouseenter={(e) => showTooltip(e, b, i)}
					onmouseleave={hideTooltip}
				/>
			{/each}
		</svg>
	{/if}
</div>

{#if tooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium tabular-nums"
		style="top: {tooltip.top}px; left: {tooltip.left}px;"
	>
		{tooltip.text}
	</span>
{/if}
