<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { Space } from '$lib/api/types';
	import { portal } from '$lib/actions/portal';
	import { clockTime } from '$lib/omni/layers';

	let {
		version,
		generatedAt,
		snapshotType,
		spaces,
		activeSpaceId,
		resynthesizing,
		isViewingOlder,
		onSpaceChange,
		onResynthesis,
		onJumpToLatest
	}: {
		version: number;
		generatedAt: string | null;
		snapshotType: string | null;
		spaces: Space[];
		activeSpaceId: string;
		resynthesizing: boolean;
		isViewingOlder: boolean;
		onSpaceChange: (spaceId: string) => void;
		onResynthesis: () => void;
		onJumpToLatest: () => void;
	} = $props();

	let spaceOpen = $state(false);
	let spaceAnchor = $state<HTMLButtonElement | null>(null);
	let spacePos = $state<{ top: number; right: number } | null>(null);
	const activeSpace = $derived(spaces.find((s) => s.space_id === activeSpaceId));
	const stamp = $derived(clockTime(generatedAt));

	// The menu is portalled to <body> and positioned in viewport coordinates.
	// It cannot be `position: absolute` inside this bar: the bar is a glass
	// surface, and backdrop-filter makes a containing block AND a stacking
	// context — so any z-index here is scoped to the bar and the instrument tray
	// below (a later sibling, also frosted) paints straight over the menu.
	function placeSpaceMenu() {
		if (!spaceAnchor) return;
		const rect = spaceAnchor.getBoundingClientRect();
		spacePos = { top: rect.bottom + 4, right: window.innerWidth - rect.right };
	}

	function toggleSpaceMenu() {
		spaceOpen = !spaceOpen;
		if (spaceOpen) placeSpaceMenu();
	}

	const PILL =
		'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[calc(11px*var(--om-scale))] transition-colors';
</script>

<!-- The menu holds viewport coordinates, so a resize while it is open would
     leave it detached from its trigger. -->
<svelte:window
	onresize={() => spaceOpen && placeSpaceMenu()}
	onkeydown={(e) => {
		if (e.key === 'Escape') spaceOpen = false;
	}}
/>

<div
	class="om-bar om-glass flex flex-none items-center gap-3 px-[18px]"
	style="border-top: 1px solid var(--om-border);
		padding-block: calc(7px * var(--om-density));"
>
	<span
		class="text-[calc(17px*var(--om-scale))] font-bold tracking-[-0.02em] whitespace-nowrap"
		style="color: var(--om-text);"
	>
		Laya <span class="text-laya-orange">Omni</span>
	</span>

	{#if version > 0}
		<span class="om-mono text-[calc(10.5px*var(--om-scale))]" style="color: var(--om-text-meta);">
			v{version}{stamp ? ` · ${stamp}` : ''}
		</span>
		{#if snapshotType}
			<span
				class="om-badge-lg rounded px-1.5 py-0.5"
				style="background: var(--om-warn-bg); color: var(--om-warn-fg);"
			>{snapshotType.toUpperCase()}</span>
		{/if}
	{/if}

	{#if isViewingOlder}
		<!-- Without this the user gets stranded in the past — the old page had
		     exactly that problem once the fisheye scrubbed backwards. -->
		<button
			type="button"
			class="{PILL} font-semibold"
			style="border: 1px solid var(--om-comp-border); background: var(--om-comp-bg); color: var(--om-comp-num);"
			onclick={onJumpToLatest}
		>
			VIEWING v{version}
			<span style="color: var(--om-text-meta);">·</span>
			Jump to latest
		</button>
	{/if}

	<div class="flex-1"></div>

	{#if spaces.length > 1}
		<div>
			<button
				type="button"
				bind:this={spaceAnchor}
				class={PILL}
				style="border: 1px solid var(--om-border-pill); color: var(--om-text-body);"
				aria-haspopup="listbox"
				aria-expanded={spaceOpen}
				onclick={toggleSpaceMenu}
			>
				{#if activeSpace}
					<span
						class="h-1.5 w-1.5 rounded-full"
						style="background: {activeSpace.color};"
					></span>
					{activeSpace.name}
				{:else}
					All Spaces
				{/if}
				<svg class="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
				</svg>
			</button>

			{#if spaceOpen && spacePos}
				<div
					use:portal
					class="fixed inset-0 z-[110]"
					role="presentation"
					onclick={() => (spaceOpen = false)}
				></div>
				<div
					use:portal
					class="om-popover-surface fixed z-[111] min-w-[160px] overflow-hidden rounded-lg p-1"
					role="listbox"
					aria-label="Space"
					tabindex="-1"
					style="top: {spacePos.top}px; right: {spacePos.right}px;"
				>
					{#each spaces as space (space.space_id)}
						{@const selected = space.space_id === activeSpaceId}
						<button
							type="button"
							role="option"
							aria-selected={selected}
							class="om-pill-t flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 font-medium transition-colors"
							style="background: {selected ? 'var(--om-warn-bg)' : 'transparent'};
								color: {selected ? 'var(--om-warn-fg)' : 'var(--om-text-body)'};"
							onclick={() => {
								onSpaceChange(space.space_id);
								spaceOpen = false;
							}}
						>
							<span class="h-1.5 w-1.5 rounded-full" style="background: {space.color};"></span>
							{space.name}
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<button
		type="button"
		class="{PILL} disabled:cursor-not-allowed disabled:opacity-50"
		style="border: 1px solid var(--om-border-pill); color: var(--om-text-body);"
		disabled={resynthesizing}
		onclick={onResynthesis}
	>
		{#if resynthesizing}
			<svg class="h-2.5 w-2.5 animate-spin" fill="none" viewBox="0 0 24 24">
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
				<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
			</svg>
			Synthesizing…
		{:else}
			<svg class="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
				<path
					d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
				/>
			</svg>
			Resynthesize
		{/if}
	</button>
</div>
