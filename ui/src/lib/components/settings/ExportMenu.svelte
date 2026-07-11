<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Small "Export ▾" dropdown that first asks the user how far back to export,
	then invokes `onexport(days)`. days=0 means all time. Used by the Audit page
	for both the audit log and the filtered events export.
-->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';

	let {
		label = 'Export',
		onexport,
		disabled = false,
		// The leading download glyph. Off in the Audit "events filtered" banner so this
		// button matches the width of the sibling Retry/Clear buttons and its neighbouring
		// "View" lines up with the other banners' "View" (the chevron still signals a menu).
		showIcon = true
	}: {
		label?: string;
		onexport: (days: number) => void | Promise<void>;
		disabled?: boolean;
		showIcon?: boolean;
	} = $props();

	// Timeframe choices — "how far back". days=0 is the all-time sentinel.
	const OPTIONS = [
		{ label: 'Last 24 hours', days: 1 },
		{ label: 'Last 7 days', days: 7 },
		{ label: 'Last 30 days', days: 30 },
		{ label: 'Last 90 days', days: 90 },
		{ label: 'All time', days: 0 }
	];

	let open = $state(false);
	let busy = $state(false);
	let rootRef = $state<HTMLElement | null>(null);

	async function choose(days: number) {
		open = false;
		busy = true;
		try {
			await onexport(days);
		} finally {
			busy = false;
		}
	}

	function onWindowClick(e: MouseEvent) {
		if (!open) return;
		if (rootRef && !rootRef.contains(e.target as Node)) open = false;
	}
</script>

<svelte:window onclick={onWindowClick} />

<div class="relative" bind:this={rootRef}>
	<button
		type="button"
		onclick={() => { if (!busy) open = !open; }}
		disabled={disabled || busy}
		class="flex items-center gap-1.5 rounded px-2 py-1 text-laya-secondary font-medium text-surface-300 transition-colors disabled:opacity-50 {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}"
	>
		{#if busy}
			<span class="h-3 w-3 animate-spin rounded-full border-2 border-surface-400 border-t-transparent"></span>
			Exporting…
		{:else}
			{#if showIcon}
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
				</svg>
			{/if}
			{label}
			<svg class="h-3 w-3 transition-transform {open ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		{/if}
	</button>

	{#if open}
		<div
			class="absolute right-0 z-50 mt-1 min-w-[170px] overflow-hidden rounded-lg border p-1 {$glassTheme ? 'glass-dropdown border-white/15' : 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
			role="menu"
		>
			<div class="px-2.5 py-1 text-[11px] uppercase tracking-wide text-surface-500">How far back?</div>
			{#each OPTIONS as opt}
				<button
					type="button"
					role="menuitem"
					onclick={() => choose(opt.days)}
					class="block w-full rounded-md px-2.5 py-1.5 text-left text-sm text-surface-300 transition-colors {$glassTheme ? 'hover:bg-white/[0.06]' : 'hover:bg-surface-700'}"
				>
					{opt.label}
				</button>
			{/each}
		</div>
	{/if}
</div>
