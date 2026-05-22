<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
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

<div class="rounded-xl border p-5 {$glassTheme ? 'glass-section' : 'border-surface-700 bg-surface-800'}">
	<div class="mb-1 text-xs uppercase tracking-wider text-surface-400">{label}</div>
	<div class="text-2xl font-bold {color ?? 'text-surface-50'}">{value}</div>
	{#if subtitle}
		<div class="mt-1 text-xs text-surface-500">{subtitle}</div>
	{/if}
</div>
