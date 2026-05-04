<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	let { data, title }: { data: { label: string; value: number }[]; title?: string } = $props();

	const maxValue = $derived(Math.max(...data.map((d) => d.value)) || 1);
	const total = $derived(data.reduce((s, d) => s + d.value, 0));

	let tooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showTooltip(e: MouseEvent, item: { label: string; value: number }) {
		const el = (e.currentTarget as HTMLElement).querySelector('.bar-track') as HTMLElement;
		if (!el) return;
		const r = el.getBoundingClientRect();
		tooltip = { text: `${item.label}: ${item.value}`, top: r.top - 28, left: r.left + r.width / 2 };
	}
	function hideTooltip() { tooltip = null; }

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

<div class="rounded-xl border p-5 {$glassTheme ? 'glass-section' : 'border-surface-700 bg-surface-800'}">
	{#if title}
		<h3 class="mb-4 text-xs font-semibold uppercase tracking-wider text-surface-400">{title}</h3>
	{/if}

	{#if data.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<div class="space-y-2">
			{#each data as item, i}
				{@const pct = (item.value / maxValue) * 100}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative flex items-center gap-3"
					onmouseenter={(e) => showTooltip(e, item)}
					onmouseleave={hideTooltip}
				>
					<span class="w-[45%] min-w-0 truncate text-xs text-surface-300">{item.label}</span>
					<div class="relative flex-1 bar-track">
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

{#if tooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {tooltip.top}px; left: {tooltip.left}px;"
	>
		{tooltip.text}
	</span>
{/if}
