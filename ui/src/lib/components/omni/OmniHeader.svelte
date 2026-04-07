<script lang="ts">
	import type { OmniHistoryEntry, OmniStats, Space } from '$lib/api/types';

	let {
		version,
		generatedAt,
		snapshotType,
		stats,
		history,
		resynthesizing,
		spaces,
		activeSpaceId,
		onVersionChange,
		onResynthesis,
		onSpaceChange
	}: {
		version: number;
		generatedAt: string | null;
		snapshotType: string | null;
		stats: OmniStats;
		history: OmniHistoryEntry[];
		resynthesizing: boolean;
		spaces: Space[];
		activeSpaceId: string;
		onVersionChange: (version: number) => void;
		onResynthesis: () => void;
		onSpaceChange: (spaceId: string) => void;
	} = $props();

	// Slider preview: tracks the version being hovered/dragged before release
	let previewVersion = $state<number | null>(null);

	function formatTimestamp(iso: string | null): string {
		if (!iso) return 'Never';
		const d = new Date(iso);
		const now = new Date();
		const diffMs = now.getTime() - d.getTime();
		const diffMins = Math.floor(diffMs / 60000);
		if (diffMins < 1) return 'Just now';
		if (diffMins < 60) return `${diffMins}m ago`;
		const diffHours = Math.floor(diffMins / 60);
		if (diffHours < 24) return `${diffHours}h ago`;
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	function formatDate(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
	}

	/** Look up a history entry by version number. */
	function findEntry(v: number): OmniHistoryEntry | undefined {
		return history.find((h) => h.version === v);
	}

	const minVersion = $derived(history.length > 0 ? history[history.length - 1].version : 1);
	const maxVersion = $derived(history.length > 0 ? history[0].version : version);

	// The label shown while dragging — either the preview or current version
	const activeEntry = $derived(findEntry(previewVersion ?? version));
	const activeLabel = $derived(activeEntry ? formatDate(activeEntry.generated_at) : formatTimestamp(generatedAt));
	const activeType = $derived(activeEntry?.snapshot_type ?? snapshotType);
</script>

<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
	<!-- Left: Title + meta -->
	<div>
		<div class="flex items-center gap-3">
			<h1 class="text-xl font-bold text-surface-50">Laya <span class="text-laya-orange">Omni</span></h1>
			{#if version > 0}
				<div class="flex items-center gap-2 text-xs text-surface-500">
					<span>v{previewVersion ?? version}</span>
					<span class="text-surface-700">|</span>
					<span>{activeLabel}</span>
					{#if activeType}
						<span class="rounded bg-surface-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-surface-400">
							{activeType}
						</span>
					{/if}
				</div>
			{/if}
		</div>
		<p class="text-xs text-surface-500 mt-0.5">The big picture, at a glance</p>
	</div>

	<!-- Right: Controls -->
	<div class="flex items-center gap-2">
		<!-- Space pills -->
		{#if spaces.length > 1}
			<div class="flex items-center gap-0.5 rounded-lg border border-surface-700 overflow-hidden">
				{#each spaces as space}
					<button
						onclick={() => onSpaceChange(space.space_id)}
						class="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium transition-colors
							{activeSpaceId === space.space_id
								? 'bg-laya-orange/15 text-laya-orange'
								: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
					>
						<span
							class="inline-block h-1.5 w-1.5 rounded-full"
							style="background-color: {space.color}"
						></span>
						{space.name}
					</button>
				{/each}
			</div>
		{/if}

		<!-- Resynthesis button -->
		<button
			onclick={onResynthesis}
			disabled={resynthesizing}
			class="flex items-center gap-1.5 rounded-lg border border-surface-700 px-3 py-1.5 text-xs font-medium text-surface-300 transition-colors hover:border-laya-orange/30 hover:text-laya-orange disabled:opacity-50 disabled:cursor-not-allowed"
		>
			{#if resynthesizing}
				<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
					<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
					<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
				</svg>
				Synthesizing...
			{:else}
				<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
				</svg>
				Resynthesize
			{/if}
		</button>
	</div>
</div>

<!-- Stats bar -->
{#if stats.events_processed > 0}
	<div class="mt-2 flex items-center gap-4 text-[11px] text-surface-500">
		<span>{stats.events_processed} events processed</span>
		{#if stats.cards_acted_on > 0}
			<span>{stats.cards_acted_on} user actions</span>
		{/if}
		{#if stats.compression_ratio > 0}
			<span>{Math.round(stats.compression_ratio * 100)}% distilled</span>
		{/if}
	</div>
{/if}

<!-- Timeline scale — separate row below stats, only when there's history to navigate -->
{#if history.length > 1}
	{@const displayEntries = history.slice().reverse()}
	<div class="mt-3 rounded-lg border border-surface-700 bg-surface-800/50 px-4 py-3">
		<!-- Label row -->
		<div class="flex items-center justify-between mb-2">
			<span class="text-[11px] font-medium text-surface-400">
				<svg class="inline h-3 w-3 mr-0.5 -mt-px" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
				</svg>
				Timeline
			</span>
			<span class="text-[10px] text-surface-500">
				{#if previewVersion != null}
					{@const pe = findEntry(previewVersion)}
					v{previewVersion} — {pe ? formatDate(pe.generated_at) : ''}
					{#if pe?.snapshot_type}
						<span class="ml-1 uppercase tracking-wider">{pe.snapshot_type}</span>
					{/if}
				{:else}
					Viewing v{version}
				{/if}
			</span>
		</div>

		<!-- Scale track -->
		<div class="relative flex items-center h-5">
			<!-- Track line -->
			<div class="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-px bg-surface-600"></div>

			<!-- Tick marks + dots -->
			{#each displayEntries as entry, i}
				{@const isActive = entry.version === (previewVersion ?? version)}
				{@const isSynthesis = entry.snapshot_type === 'scheduled' || entry.snapshot_type === 'rolling' || entry.snapshot_type === 'manual'}
				{@const isLatest = i === displayEntries.length - 1}
				{@const pct = displayEntries.length > 1 ? (i / (displayEntries.length - 1)) * 100 : 50}
				<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
				<div
					class="absolute -translate-x-1/2 flex flex-col items-center cursor-pointer group/tick"
					style="left: {pct}%"
					onclick={() => { previewVersion = null; onVersionChange(entry.version); }}
					onmouseenter={() => { previewVersion = entry.version; }}
					onmouseleave={() => { previewVersion = null; }}
				>
					<!-- Dot with wider hit area; synthesis snapshots are larger + orange-tinted -->
					<div class="px-1 py-1.5">
						<div class="rounded-full transition-colors {isSynthesis ? 'w-2 h-2' : 'w-1.5 h-1.5'} {isActive ? 'bg-laya-orange' : isSynthesis ? 'bg-laya-orange/40 group-hover/tick:bg-laya-orange/70' : 'bg-surface-600 group-hover/tick:bg-surface-300'}"></div>
					</div>
				</div>
			{/each}
		</div>

		<!-- Edge labels -->
		<div class="flex items-center justify-between mt-1">
			<span class="text-[9px] text-surface-500">{formatDate(displayEntries[0]?.generated_at)}</span>
			<span class="text-[9px] text-surface-400 font-medium">Latest</span>
		</div>
	</div>
{/if}
