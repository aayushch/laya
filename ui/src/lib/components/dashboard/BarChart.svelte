<script lang="ts">
	let { data, title }: { data: { label: string; value: number }[]; title?: string } = $props();

	const maxValue = $derived(Math.max(...data.map((d) => d.value), 1));
	const total = $derived(data.reduce((s, d) => s + d.value, 0));

	const barColors = [
		'fill-blue-500',
		'fill-emerald-500',
		'fill-violet-500',
		'fill-amber-500',
		'fill-red-500',
		'fill-cyan-500',
		'fill-pink-500'
	];
</script>

<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
	{#if title}
		<h3 class="mb-4 text-xs font-semibold uppercase tracking-wider text-surface-400">{title}</h3>
	{/if}

	{#if data.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<div class="space-y-2.5">
			{#each data as item, i}
				{@const pct = (item.value / maxValue) * 100}
				<div>
					<div class="mb-1 flex items-center justify-between text-xs">
						<span class="text-surface-300">{item.label}</span>
						<span class="text-surface-400">{item.value}</span>
					</div>
					<svg viewBox="0 0 200 12" class="w-full">
						<rect x="0" y="0" width="200" height="12" rx="4" class="fill-surface-700" />
						<rect
							x="0"
							y="0"
							width={pct * 2}
							height="12"
							rx="4"
							class={barColors[i % barColors.length]}
						/>
					</svg>
				</div>
			{/each}
		</div>
		<div class="mt-3 text-xs text-surface-500">Total: {total}</div>
	{/if}
</div>
