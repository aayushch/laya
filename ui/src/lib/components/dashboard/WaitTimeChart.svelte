<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import type { ThroughputBucket } from '$lib/api/types';
	import { monotonePath, monotoneAreaPath, niceStep, formatBucketLabel } from './chartUtils';

	let { buckets, title = 'Queue Wait Time', windowMinutes = 60 }: { buckets: ThroughputBucket[]; title?: string; windowMinutes?: number } = $props();

	const COLOR_AVG = 'var(--color-laya-gold)';
	const COLOR_P95 = 'var(--color-laya-coral)';
	const GRAD_ID = 'wt-avg-grad';

	const padL = 38;
	const padR = 12;
	const padT = 12;
	const padB = 24;
	const W = 800;
	const H = 150;
	const chartW = W - padL - padR;
	const chartH = H - padT - padB;
	const baseline = padT + chartH;
	const slotW = $derived(chartW / buckets.length);

	const rawMax = $derived(
		Math.max(...buckets.map((b) => b.p95_wait_s), ...buckets.map((b) => b.avg_wait_s), 0.1)
	);
	const step = $derived(niceStep(rawMax, 4));
	const maxY = $derived(Math.ceil(rawMax / step) * step || step);
	const labelEvery = $derived(Math.max(1, Math.ceil(buckets.length / 10)));

	const yTicks = $derived.by(() => {
		const ticks: number[] = [];
		for (let v = 0; v <= maxY; v += step) ticks.push(v);
		return ticks;
	});

	function formatTick(s: number): string {
		if (s === 0) return '0s';
		if (s < 60) return `${Math.round(s)}s`;
		const m = Math.floor(s / 60);
		const rem = Math.round(s % 60);
		return rem > 0 ? `${m}m${rem}s` : `${m}m`;
	}

	function formatTooltip(s: number): string {
		if (s < 0.1) return '0.0s';
		if (s < 60) return `${s.toFixed(1)}s`;
		const m = Math.floor(s / 60);
		const rem = s % 60;
		return `${m}m ${rem.toFixed(0)}s`;
	}

	function xMid(i: number): number {
		return padL + i * slotW + slotW / 2;
	}

	function y(v: number): number {
		return padT + chartH - (v / maxY) * chartH;
	}

	// Smooth curves
	const avgPts = $derived(buckets.map((b, i) => ({ x: xMid(i), y: y(b.avg_wait_s) })));
	const p95Pts = $derived(buckets.map((b, i) => ({ x: xMid(i), y: y(b.p95_wait_s) })));
	const avgLine = $derived(monotonePath(avgPts));
	const p95Line = $derived(monotonePath(p95Pts));
	const avgArea = $derived(monotoneAreaPath(avgPts, baseline));

	// Fill band between p95 and avg (polygon using raw points — at 60+ vertices looks smooth)
	const bandPoly = $derived.by(() => {
		if (buckets.length < 2) return '';
		const top = buckets.map((b, i) => `${xMid(i).toFixed(1)},${y(b.p95_wait_s).toFixed(1)}`);
		const btm = [...buckets]
			.reverse()
			.map((b, j) => {
				const origI = buckets.length - 1 - j;
				return `${xMid(origI).toFixed(1)},${y(b.avg_wait_s).toFixed(1)}`;
			});
		return [...top, ...btm].join(' ');
	});

	let hoverIdx = $state<number | null>(null);
	let tooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showTooltip(e: MouseEvent, b: ThroughputBucket, i: number) {
		const el = e.currentTarget as SVGElement;
		const rect = el.getBoundingClientRect();
		hoverIdx = i;
		tooltip = {
			text: `${formatBucketLabel(b.minute, windowMinutes)} — Avg: ${formatTooltip(b.avg_wait_s)}, P95: ${formatTooltip(b.p95_wait_s)}`,
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
					<span class="inline-block h-[3px] w-3 rounded-full" style="background: {COLOR_AVG}"></span>
					Avg
				</span>
				<span class="flex items-center gap-1.5">
					<span class="inline-block h-[3px] w-3 rounded-full opacity-50" style="background: {COLOR_P95}"></span>
					P95
				</span>
			</div>
		</div>
	{/if}

	{#if buckets.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<svg viewBox="0 0 {W} {H}" class="w-full" preserveAspectRatio="xMidYMid meet">
			<defs>
				<linearGradient id={GRAD_ID} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" style="stop-color: var(--color-laya-gold)" stop-opacity="0.12" />
					<stop offset="100%" style="stop-color: var(--color-laya-gold)" stop-opacity="0.01" />
				</linearGradient>
			</defs>

			<!-- Horizontal grid lines (skip baseline) -->
			{#each yTicks as tick}
				{#if tick > 0}
					<line
						x1={padL}
						y1={y(tick)}
						x2={W - padR}
						y2={y(tick)}
						stroke="currentColor"
						class="text-surface-700/30"
						stroke-width="0.5"
					/>
				{/if}
				<text
					x={padL - 7}
					y={y(tick) + 3}
					text-anchor="end"
					class="fill-surface-500"
					font-size="8"
					font-family="system-ui, sans-serif"
				>{formatTick(tick)}</text>
			{/each}

			<!-- Avg area gradient fill -->
			{#if avgArea}
				<path d={avgArea} fill="url(#{GRAD_ID})" />
			{/if}

			<!-- P95/Avg fill band -->
			{#if bandPoly}
				<polygon points={bandPoly} fill={COLOR_P95} opacity="0.07" />
			{/if}

			<!-- P95 smooth line -->
			{#if p95Line}
				<path
					d={p95Line}
					fill="none"
					stroke={COLOR_P95}
					stroke-width="1"
					stroke-dasharray="4 3"
					stroke-linecap="round"
					stroke-linejoin="round"
					opacity="0.4"
				/>
			{/if}

			<!-- Avg smooth line -->
			{#if avgLine}
				<path
					d={avgLine}
					fill="none"
					stroke={COLOR_AVG}
					stroke-width="1.5"
					stroke-linecap="round"
					stroke-linejoin="round"
					opacity="0.85"
				/>
			{/if}

			<!-- X-axis baseline -->
			<line
				x1={padL}
				y1={baseline}
				x2={W - padR}
				y2={baseline}
				stroke="currentColor"
				class="text-surface-600"
				stroke-width="0.75"
			/>

			<!-- Hover guide + dots -->
			{#if hoverIdx !== null}
				{@const b = buckets[hoverIdx]}
				<line
					x1={xMid(hoverIdx)}
					y1={padT}
					x2={xMid(hoverIdx)}
					y2={baseline}
					stroke="currentColor"
					class="text-surface-500/50"
					stroke-width="0.5"
					stroke-dasharray="3 2"
				/>
				<circle cx={xMid(hoverIdx)} cy={y(b.avg_wait_s)} r="3" fill={COLOR_AVG} />
				{#if b.p95_wait_s > 0}
					<circle cx={xMid(hoverIdx)} cy={y(b.p95_wait_s)} r="2.5" fill={COLOR_P95} opacity="0.6" />
				{/if}
			{/if}

			<!-- X-axis labels -->
			{#each buckets as b, i}
				{#if i % labelEvery === 0}
					<text
						x={xMid(i)}
						y={baseline + 14}
						text-anchor="middle"
						class="fill-surface-500"
						font-size="8"
						font-family="system-ui, sans-serif"
					>{formatBucketLabel(b.minute, windowMinutes)}</text>
				{/if}
			{/each}

			<!-- Invisible hover rects -->
			{#each buckets as b, i}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<rect
					x={padL + i * slotW}
					y={padT}
					width={slotW}
					height={chartH}
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
