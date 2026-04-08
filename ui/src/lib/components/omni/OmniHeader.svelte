<script lang="ts">
	import type { TimelineSegment, TimelineEntry, OmniStats, Space } from '$lib/api/types';

	let {
		version,
		generatedAt,
		snapshotType,
		stats,
		segments,
		resynthesizing,
		nextSynthesis,
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
		segments: TimelineSegment[];
		resynthesizing: boolean;
		nextSynthesis: string | null;
		spaces: Space[];
		activeSpaceId: string;
		onVersionChange: (version: number) => void;
		onResynthesis: () => void;
		onSpaceChange: (spaceId: string) => void;
	} = $props();

	let previewVersion = $state<number | null>(null);

	// Flatten all entries for lookups
	const allEntries = $derived(segments.flatMap(s => s.entries));
	const totalEntryCount = $derived(allEntries.length);

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

	function findEntry(v: number): TimelineEntry | undefined {
		return allEntries.find((e) => e.version === v);
	}

	const activeEntry = $derived(findEntry(previewVersion ?? version));
	const activeLabel = $derived(activeEntry ? formatDate(activeEntry.generated_at) : formatTimestamp(generatedAt));
	const activeType = $derived(activeEntry?.snapshot_type ?? snapshotType);

	// Compute proportional segment widths (min 12% each for non-empty, 0 for empty)
	const segmentWidths = $derived(() => {
		const nonEmpty = segments.filter(s => s.entries.length > 0);
		if (nonEmpty.length === 0) return segments.map(() => 0);

		const weights = segments.map(s => Math.max(s.entries.length, 0));
		const totalWeight = weights.reduce((a, b) => a + b, 0) || 1;
		const minPct = 12;
		const raw = weights.map(w => w > 0 ? Math.max((w / totalWeight) * 100, minPct) : 0);
		const rawTotal = raw.reduce((a, b) => a + b, 0) || 1;
		return raw.map(r => (r / rawTotal) * 100);
	});

	/** Position a dot within its segment's time range based on actual timestamp. */
	function entryPct(entry: TimelineEntry, seg: TimelineSegment, idx: number, count: number): number {
		if (count <= 1) return 50;
		const segStart = seg.range_start ? new Date(seg.range_start).getTime() : new Date(entry.generated_at).getTime();
		const segEnd = seg.range_end ? new Date(seg.range_end).getTime() : Date.now();
		const entryTime = new Date(entry.generated_at).getTime();
		const range = segEnd - segStart;
		if (range <= 0) return (idx / (count - 1)) * 100;
		// Clamp to 2%-98% so dots don't sit on the edge/divider
		return Math.max(2, Math.min(98, ((entryTime - segStart) / range) * 100));
	}

	function isSynthesis(type: string): boolean {
		return type === 'scheduled' || type === 'rolling' || type === 'manual';
	}
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
{#if stats.events_processed > 0 || nextSynthesis}
	<div class="mt-2 flex items-center gap-4 text-[11px] text-surface-500">
		{#if stats.events_processed > 0}
			<span>{stats.events_processed} events processed</span>
		{/if}
		{#if stats.cards_acted_on > 0}
			<span>{stats.cards_acted_on} user actions</span>
		{/if}
		{#if stats.compression_ratio > 0}
			<span>{Math.round(stats.compression_ratio * 100)}% distilled</span>
		{/if}
		{#if nextSynthesis}
			<span class="text-surface-400">Next synthesis {nextSynthesis}</span>
		{/if}
	</div>
{/if}

<!-- Segmented Timeline -->
{#if totalEntryCount > 1}
	{@const widths = segmentWidths()}
	{@const nonEmptySegments = segments.filter(s => s.entries.length > 0)}
	<div class="mt-3 rounded-lg border border-surface-700 bg-surface-800/50 px-4 py-3">
		<!-- Header row -->
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

		<!-- Segment labels -->
		<div class="flex mb-1">
			{#each segments as seg, si}
				{#if seg.entries.length > 0}
					{#if si > 0 && segments.slice(0, si).some(s => s.entries.length > 0)}
						<div class="w-px"></div>
					{/if}
					<div style="width: {widths[si]}%" class="text-center">
						<span class="text-[9px] uppercase tracking-wider text-surface-500">{seg.label}</span>
					</div>
				{/if}
			{/each}
		</div>

		<!-- Track with segments -->
		<div class="flex items-center h-5">
			{#each segments as seg, si}
				{#if seg.entries.length > 0}
					<!-- Divider between segments -->
					{#if si > 0 && segments.slice(0, si).some(s => s.entries.length > 0)}
						<div class="w-px h-4 bg-surface-600 flex-shrink-0"></div>
					{/if}
					<!-- Segment track -->
					<div class="relative h-5" style="width: {widths[si]}%">
						<!-- Track line -->
						<div class="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-px bg-surface-600"></div>

						{#each seg.entries as entry, ei}
							{@const isActive = entry.version === (previewVersion ?? version)}
							{@const isSynth = isSynthesis(entry.snapshot_type)}
							{@const pct = entryPct(entry, seg, ei, seg.entries.length)}
							<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
							<div
								class="absolute -translate-x-1/2 flex flex-col items-center cursor-pointer group/tick"
								style="left: {pct}%"
								onclick={() => { previewVersion = null; onVersionChange(entry.version); }}
								onmouseenter={() => { previewVersion = entry.version; }}
								onmouseleave={() => { previewVersion = null; }}
							>
								<div class="px-1 py-1.5">
									<div class="rounded-full transition-colors
										{isSynth ? 'w-2 h-2' : 'w-1.5 h-1.5'}
										{isActive
											? 'bg-laya-orange ring-2 ring-laya-orange/30'
											: isSynth
												? 'bg-laya-orange/40 group-hover/tick:bg-laya-orange/70'
												: 'bg-surface-500 group-hover/tick:bg-surface-300'}
									"></div>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			{/each}
		</div>

		<!-- Edge labels -->
		<div class="flex items-center justify-between mt-1">
			<span class="text-[9px] text-surface-500">
				{#if nonEmptySegments.length > 0 && nonEmptySegments[0].entries.length > 0}
					{formatDate(nonEmptySegments[0].entries[0].generated_at)}
				{/if}
			</span>
			<span class="text-[9px] text-surface-400 font-medium">Now</span>
		</div>
	</div>
{/if}
