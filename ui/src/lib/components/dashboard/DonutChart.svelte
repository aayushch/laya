<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	let {
		data,
		title,
		size = 140
	}: {
		data: { label: string; value: number; color: string }[];
		title?: string;
		size?: number;
	} = $props();

	const total = $derived(data.reduce((s, d) => s + d.value, 0));
	const radius = 50;
	const circumference = 2 * Math.PI * radius;

	const segments = $derived(() => {
		let offset = 0;
		return data.map((d) => {
			const pct = total > 0 ? d.value / total : 0;
			const dashLen = pct * circumference;
			const seg = { ...d, pct, dashLen, dashOffset: -offset };
			offset += dashLen;
			return seg;
		});
	});
</script>

<div class="rounded-xl border p-5 {$glassTheme ? 'glass-section' : 'border-surface-700 bg-surface-800'}">
	{#if title}
		<h3 class="mb-4 text-xs font-semibold uppercase tracking-wider text-surface-400">{title}</h3>
	{/if}

	{#if data.length === 0 || total === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<div class="flex items-center gap-6">
			<svg width={size} height={size} viewBox="0 0 120 120" class="flex-shrink-0">
				{#each segments() as seg}
					<circle
						cx="60"
						cy="60"
						r={radius}
						fill="none"
						stroke={seg.color}
						stroke-width="16"
						stroke-dasharray="{seg.dashLen} {circumference - seg.dashLen}"
						stroke-dashoffset={seg.dashOffset}
						transform="rotate(-90 60 60)"
					/>
				{/each}
				<text x="60" y="60" text-anchor="middle" dominant-baseline="central" class="fill-surface-200 text-lg font-bold" font-size="18">
					{total}
				</text>
			</svg>

			<div class="space-y-1.5">
				{#each data as item}
					<div class="flex items-center gap-2 text-xs">
						<span class="h-2.5 w-2.5 rounded-full flex-shrink-0" style="background: {item.color}"></span>
						<span class="text-surface-300">{item.label}</span>
						<span class="text-surface-500">({item.value})</span>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
