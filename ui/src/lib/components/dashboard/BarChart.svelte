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
				<div class="flex items-center gap-3">
					<span class="w-[45%] min-w-0 truncate text-xs text-surface-300" title={item.label}>{item.label}</span>
					<div class="flex-1">
						<div class="h-2 overflow-hidden rounded-full bg-surface-700">
							<div
								class="h-full rounded-full {barColors[i % barColors.length]}"
								style="width: {pct}%"
							></div>
						</div>
					</div>
					<span class="w-12 text-right text-xs tabular-nums text-surface-400">{item.value}</span>
				</div>
			{/each}
		</div>
		<div class="mt-3 text-xs text-surface-500">Total: {total}</div>
	{/if}
</div>
