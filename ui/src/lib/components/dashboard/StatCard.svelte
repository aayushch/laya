<script lang="ts">
	let {
		label,
		value,
		subtitle,
		color
	}: {
		label: string;
		value: string;
		subtitle?: string;
		color?: string;
	} = $props();
</script>

<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
	<div class="mb-1 text-xs uppercase tracking-wider text-surface-400">{label}</div>
	<div class="text-2xl font-bold {color ?? 'text-surface-50'}">{value}</div>
	{#if subtitle}
		<div class="mt-1 text-xs text-surface-500">{subtitle}</div>
	{/if}
</div>
