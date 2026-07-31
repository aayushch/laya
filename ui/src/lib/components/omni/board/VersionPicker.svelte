<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { TimelineEntry } from '$lib/api/types';
	import { portal } from '$lib/actions/portal';
	import { clockTime } from '$lib/omni/layers';

	let {
		value,
		entries,
		variant,
		caption,
		disabled = false,
		onSelect,
		onFullHistory
	}: {
		value: number;
		/** Selectable versions, newest first. */
		entries: TimelineEntry[];
		/** `base` = the rail's own subject (accented). `display` = the board's version. */
		variant: 'base' | 'display';
		caption: string;
		disabled?: boolean;
		onSelect: (version: number) => void;
		onFullHistory?: () => void;
	} = $props();

	// Snapshot-type badges. The distinction is the point: comparing against a
	// SCHED base shows a full resynthesis diff, against an INCR base only what
	// the queue appended since.
	const TYPE_BADGE: Record<string, { label: string; bg: string; fg: string }> = {
		incremental: { label: 'INCR', bg: 'var(--om-neutral-bg)', fg: 'var(--om-neutral-fg)' },
		rolling: { label: 'ROLL', bg: 'var(--om-warn-bg)', fg: 'var(--om-warn-fg)' },
		manual: { label: 'MAN', bg: 'var(--om-warn-bg)', fg: 'var(--om-warn-fg)' },
		scheduled: {
			label: 'SCHED',
			bg: 'color-mix(in oklch, var(--om-layer-period) 20%, transparent)',
			fg: 'var(--om-layer-period-fg)'
		}
	};

	let open = $state(false);
	let anchorEl = $state<HTMLButtonElement | null>(null);
	let position = $state<{ top: number; left: number } | null>(null);
	let highlighted = $state(0);
	// Set once the caller has pulled the complete history in; the list then
	// scrolls instead of stopping at six.
	let showAll = $state(false);

	const POPOVER_WIDTH = 214;
	// Six entries plus the caption and footer — enough to reach back through a
	// working day without turning the rail into a scrolling history browser.
	const visible = $derived(showAll ? entries : entries.slice(0, 6));
	const hasMore = $derived(!showAll && entries.length > 6);

	function place() {
		if (!anchorEl) return;
		const rect = anchorEl.getBoundingClientRect();
		position = {
			top: rect.bottom + 4,
			left: Math.max(8, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - 8))
		};
	}

	function toggle() {
		if (disabled) return;
		open = !open;
		if (open) {
			place();
			highlighted = Math.max(0, visible.findIndex((e) => e.version === value));
		} else {
			showAll = false;
		}
	}

	function choose(version: number) {
		open = false;
		if (version !== value) onSelect(version);
	}

	function onKeydown(e: KeyboardEvent) {
		if (!open) return;
		if (e.key === 'Escape') {
			e.preventDefault();
			open = false;
			anchorEl?.focus();
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			highlighted = Math.min(visible.length - 1, highlighted + 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			highlighted = Math.max(0, highlighted - 1);
		} else if (e.key === 'Enter' && visible[highlighted]) {
			e.preventDefault();
			choose(visible[highlighted].version);
		}
	}

	const chrome = $derived(
		variant === 'base'
			? 'border: 1px solid var(--om-accent-border); background: var(--om-warn-bg); color: var(--om-warn-fg);'
			: 'border: 1px solid var(--om-border-input); background: var(--om-row-open); color: var(--om-text-body);'
	);
</script>

<svelte:window
	onkeydown={onKeydown}
	onresize={() => open && place()}
/>

<button
	type="button"
	bind:this={anchorEl}
	class="om-mono inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[calc(9px*var(--om-scale))] transition-opacity disabled:opacity-40"
	style={chrome}
	{disabled}
	aria-haspopup="listbox"
	aria-expanded={open}
	aria-label="{caption} (currently v{value})"
	onclick={toggle}
>
	v{value}
	<svg class="h-2 w-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
		<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
	</svg>
</button>

{#if open && position}
	<!-- Portalled: the rail is a glass surface whose backdrop-filter would trap an
	     absolutely positioned popover inside the column. -->
	<div
		use:portal
		class="fixed inset-0 z-[110]"
		role="presentation"
		onclick={() => (open = false)}
	></div>
	<div
		use:portal
		class="om-popover-surface fixed z-[111] overflow-hidden rounded-[9px]"
		role="listbox"
		aria-label={caption}
		tabindex="-1"
		style="top: {position.top}px; left: {position.left}px; width: {POPOVER_WIDTH}px;"
	>
		<div
			class="om-mono px-2.5 pt-[7px] pb-1.5 text-[calc(8px*var(--om-scale))] tracking-[0.12em] uppercase"
			style="color: var(--om-text-meta); border-bottom: 1px solid var(--om-border);"
		>{caption}</div>

		{#if visible.length === 0}
			<div class="om-pill-t px-2.5 py-3" style="color: var(--om-text-meta);">
				No other versions yet.
			</div>
		{:else}
			<div class={showAll ? 'max-h-64 overflow-y-auto' : ''}>
			{#each visible as entry, i (entry.version)}
				{@const badge = TYPE_BADGE[entry.snapshot_type] ?? TYPE_BADGE.incremental}
				{@const selected = entry.version === value}
				<button
					type="button"
					role="option"
					aria-selected={selected}
					class="flex w-full items-center gap-[7px] px-2.5 py-1.5 text-left transition-colors"
					style="background: {selected
						? 'var(--om-warn-bg)'
						: i === highlighted
							? 'var(--om-row-hover)'
							: 'transparent'};"
					onmouseenter={() => (highlighted = i)}
					onclick={() => choose(entry.version)}
				>
					<!-- min-width, not a fixed width: version numbers grow without bound
					     (this instance is past v15000) and a hard 38px slot lets a long
					     one run under the snapshot-type badge beside it. -->
					<span
						class="om-mono min-w-[38px] shrink-0 text-[calc(9.5px*var(--om-scale))] font-semibold whitespace-nowrap"
						style="color: var(--om-text-body);">v{entry.version}</span
					>
					<span
						class="rounded-[3px] px-1 py-px text-[calc(9px*var(--om-scale))] font-bold tracking-[0.07em]"
						style="background: {badge.bg}; color: {badge.fg};">{badge.label}</span
					>
					<span class="flex-1"></span>
					<span class="om-meta whitespace-nowrap">{clockTime(entry.generated_at)}</span>
					{#if selected}
						<svg
							class="h-2.5 w-2.5 shrink-0"
							style="color: var(--om-warn-fg);"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							stroke-width="3"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
						</svg>
					{:else}
						<span class="w-2.5 shrink-0"></span>
					{/if}
				</button>
			{/each}
			</div>
		{/if}

		{#if onFullHistory && hasMore}
			<button
				type="button"
				class="om-hint w-full px-2.5 py-2 text-left transition-colors"
				style="color: var(--om-text-meta); border-top: 1px solid var(--om-border);"
				onclick={() => {
					// Pull the complete list in place rather than navigating away — the
					// rail is the only time-travel control now, so leaving it to browse
					// history would strand the user off the board.
					onFullHistory?.();
					showAll = true;
				}}
			>
				Older versions in <span style="color: var(--om-comp-num);">full history…</span>
			</button>
		{/if}
	</div>
{/if}
