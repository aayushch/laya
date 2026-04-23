<script lang="ts">
	let { data, title }: { data: { label: string; value: number }[]; title?: string } = $props();

	const maxValue = $derived(Math.max(...data.map((d) => d.value)) || 1);
	const total = $derived(data.reduce((s, d) => s + d.value, 0));

	const barColors = [
		'bg-blue-500',
		'bg-emerald-500',
		'bg-violet-500',
		'bg-amber-500',
		'bg-red-500',
		'bg-cyan-500',
		'bg-pink-500'
	];
</script>

<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
	{#if title}
		<h3 class="mb-4 text-xs font-semibold uppercase tracking-wider text-surface-400">{title}</h3>
	{/if}

	{#if data.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<div class="space-y-2">
			{#each data as item, i}
				{@const pct = (item.value / maxValue) * 100}
				<div class="group relative flex items-center gap-3">
					<span class="w-[45%] min-w-0 truncate text-xs text-surface-300">{item.label}</span>
					<div class="relative flex-1">
						<div class="h-2 overflow-hidden rounded-full bg-surface-700">
							<div
								class="h-full rounded-full {barColors[i % barColors.length]}"
								style="width: {pct}%"
							></div>
						</div>
						<!-- Instant tooltip on row hover, positioned above the bar -->
						<div class="pointer-events-none absolute -top-8 left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover:block group-hover:opacity-100">
							{item.label}: {item.value}
						</div>
					</div>
					<span class="w-12 text-right text-xs tabular-nums text-surface-400">{item.value}</span>
				</div>
			{/each}
		</div>
		<div class="mt-3 text-xs text-surface-500">Total: {total}</div>
	{/if}
</div>
