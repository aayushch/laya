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
	let spaceDropdownOpen = $state(false);
	const activeSpace = $derived(spaces.find(s => s.space_id === activeSpaceId));

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
	const latestVersion = $derived(allEntries.length > 0 ? Math.max(...allEntries.map(e => e.version)) : version);
	const isViewingOlder = $derived(version < latestVersion);

	// Compute proportional segment widths (min 12% each for non-empty, 0 for empty)
	const segmentWidths = $derived.by(() => {
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

	// --- Fisheye lens distortion (Time Machine style) ---
	// The SCALE itself stretches near the cursor, spreading tick marks apart.
	let timelineEl: HTMLDivElement | undefined = $state();
	let timelineWidth = $state(800);
	let mouseRatio = $state<number | null>(null); // 0-1 across the timeline
	let rafId: number | null = null;

	function handleTimelineMove(e: MouseEvent) {
		if (!timelineEl) return;
		// Throttle to one update per animation frame for smooth motion
		if (rafId !== null) return;
		rafId = requestAnimationFrame(() => {
			rafId = null;
			if (!timelineEl) return;
			const rect = timelineEl.getBoundingClientRect();
			mouseRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
		});
	}

	function handleTimelineLeave() {
		if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
		mouseRatio = null;
		previewVersion = null;
	}

	/** Find the nearest dot to a given x-position (as % of container width). */
	function nearestDot(xPct: number): FlatDot | null {
		let best: FlatDot | null = null;
		let bestDist = Infinity;
		for (const dot of flatDots) {
			const dotPct = distortedPct(dot.segIndex, dot.localPct);
			const dist = Math.abs(dotPct - xPct);
			if (dist < bestDist) { bestDist = dist; best = dot; }
		}
		return best;
	}

	function handleDotHover(e: MouseEvent) {
		if (!timelineEl) return;
		const rect = timelineEl.getBoundingClientRect();
		const xPct = ((e.clientX - rect.left) / rect.width) * 100;
		const dot = nearestDot(xPct);
		previewVersion = dot ? dot.entry.version : null;
	}

	function handleDotClick(e: MouseEvent) {
		if (!timelineEl) return;
		const rect = timelineEl.getBoundingClientRect();
		const xPct = ((e.clientX - rect.left) / rect.width) * 100;
		const dot = nearestDot(xPct);
		if (dot) {
			previewVersion = null;
			onVersionChange(dot.entry.version);
		}
	}

	/**
	 * d3-fisheye 1D distortion: stretches positions near `focus`, compresses far away.
	 * Maps [0,1] → [0,1] preserving endpoints.
	 * factor = magnification strength (e.g. 4 → 5x zoom near cursor).
	 */
	function fisheye(x: number, focus: number, factor: number): number {
		const dx = x - focus;
		const sign = dx < 0 ? -1 : 1;
		const ad = Math.abs(dx);
		const maxD = sign > 0 ? 1 - focus : focus;
		if (maxD <= 0 || ad <= 0) return x;
		const nd = ad / maxD;
		const distorted = ((factor + 1) * nd) / (factor * nd + 1);
		return focus + sign * distorted * maxD;
	}

	/** Compute the global position (0-1) of a dot across the full timeline. */
	function toGlobal(segIndex: number, localPct: number): number {
		const w = segmentWidths;
		let offset = 0;
		for (let i = 0; i < segIndex; i++) offset += w[i];
		return (offset + (localPct / 100) * w[segIndex]) / 100;
	}

	/** Compute the distorted CSS left% for a dot, applying fisheye when hovering. */
	function distortedPct(segIndex: number, localPct: number): number {
		const globalPos = toGlobal(segIndex, localPct);
		if (mouseRatio === null) return globalPos * 100;
		return fisheye(globalPos, mouseRatio, 10) * 100;
	}

	// Precompute segment divider positions (cumulative width boundaries)
	// Left/right edges of each segment in global % coordinates
	const segmentEdges = $derived.by(() => {
		const w = segmentWidths;
		let acc = 0;
		return segments.map((_, i) => {
			const left = acc;
			acc += w[i];
			return { left, right: acc };
		});
	});

	// Cumulative width boundaries where segment dividers appear
	const dividerPositions = $derived.by(() => {
		const w = segmentWidths;
		const positions: number[] = [];
		let acc = 0;
		const nonEmptyIndices = segments.map((s, i) => s.entries.length > 0 ? i : -1).filter(i => i >= 0);
		for (let ni = 0; ni < nonEmptyIndices.length; ni++) {
			const si = nonEmptyIndices[ni];
			acc += w[si];
			// Add divider after each non-empty segment except the last
			if (ni < nonEmptyIndices.length - 1) {
				positions.push(acc);
			}
		}
		return positions;
	});

	// Hour ticks for the "Today" segment — shows time markers so users see busy periods
	interface HourTick {
		hour: number;       // 0-23
		label: string;      // e.g. "9am", "2pm"
		globalPct: number;  // position in global timeline %
	}

	const todayHourTicks = $derived.by((): HourTick[] => {
		const todaySeg = segments.find(s => s.tier === 'today');
		const todayIdx = segments.findIndex(s => s.tier === 'today');
		if (!todaySeg || todaySeg.entries.length === 0 || todayIdx < 0) return [];

		const segStart = todaySeg.range_start ? new Date(todaySeg.range_start).getTime() : Date.now() - 86400000;
		const segEnd = todaySeg.range_end ? new Date(todaySeg.range_end).getTime() : Date.now();
		const range = segEnd - segStart;
		if (range <= 0) return [];

		const allTicks: HourTick[] = [];
		// Walk through each hour boundary within the segment's range
		const startDate = new Date(segStart);
		const firstHour = new Date(startDate);
		firstHour.setMinutes(0, 0, 0);
		if (firstHour.getTime() < segStart) firstHour.setHours(firstHour.getHours() + 1);

		const w = segmentWidths;
		let segOffset = 0;
		for (let i = 0; i < todayIdx; i++) segOffset += w[i];

		for (let t = firstHour.getTime(); t < segEnd; t += 3600000) {
			const localPct = ((t - segStart) / range) * 100;
			const clampedPct = Math.max(2, Math.min(98, localPct));
			const globalPct = (segOffset + (clampedPct / 100) * w[todayIdx]);
			const hour = new Date(t).getHours();
			const label = `${hour.toString().padStart(2, '0')}:00`;
			allTicks.push({ hour, label, globalPct });
		}

		// Show all labels when fisheye distortion is active (hovering stretches the area).
		// Thin out ticks at rest when the segment is too narrow for all labels.
		if (mouseRatio !== null) return allTicks;

		const containerPx = timelineWidth;
		const segPx = (w[todayIdx] / 100) * containerPx;
		const labelWidth = 32;
		const maxLabels = Math.max(1, Math.floor(segPx / labelWidth));
		if (allTicks.length <= maxLabels) return allTicks;

		const step = Math.ceil(allTicks.length / maxLabels);
		return allTicks.filter((_, i) => i % step === 0);
	});

	// Build flat list of all dots with global positions for the single-container rendering
	interface FlatDot {
		entry: TimelineEntry;
		segIndex: number;
		localPct: number;
		isSynth: boolean;
	}

	const flatDots = $derived.by((): FlatDot[] => {
		const dots: FlatDot[] = [];
		for (let si = 0; si < segments.length; si++) {
			const seg = segments[si];
			if (seg.entries.length === 0) continue;
			for (let ei = 0; ei < seg.entries.length; ei++) {
				const entry = seg.entries[ei];
				dots.push({
					entry,
					segIndex: si,
					localPct: entryPct(entry, seg, ei, seg.entries.length),
					isSynth: isSynthesis(entry.snapshot_type),
				});
			}
		}
		return dots;
	});
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
		<!-- Space dropdown -->
		{#if spaces.length > 1}
			<div class="relative">
				<button
					onclick={() => spaceDropdownOpen = !spaceDropdownOpen}
					class="flex items-center gap-2 rounded-lg border border-surface-700 px-3 py-1.5 text-xs font-medium transition-colors hover:border-laya-orange/30
						{spaceDropdownOpen ? 'border-laya-orange/30 text-laya-orange' : 'text-surface-300'}"
				>
					{#if activeSpace}
						<span class="inline-block h-1.5 w-1.5 rounded-full" style="background-color: {activeSpace.color}"></span>
						{activeSpace.name}
					{:else}
						All Spaces
					{/if}
					<svg class="h-3 w-3 text-surface-500 transition-transform {spaceDropdownOpen ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
					</svg>
				</button>

				{#if spaceDropdownOpen}
					<!-- Backdrop to close on click outside -->
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="fixed inset-0 z-40" onclick={() => spaceDropdownOpen = false} onkeydown={() => {}}></div>
					<div class="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-lg border border-surface-700 bg-surface-900 py-1 shadow-xl">
						{#each spaces as space}
							<button
								onclick={() => { onSpaceChange(space.space_id); spaceDropdownOpen = false; }}
								class="flex w-full items-center gap-2 px-3 py-1.5 text-xs font-medium transition-colors
									{space.space_id === activeSpaceId
										? 'bg-laya-orange/10 text-laya-orange'
										: 'text-surface-300 hover:bg-surface-800 hover:text-surface-200'}"
							>
								<span class="inline-block h-1.5 w-1.5 rounded-full" style="background-color: {space.color}"></span>
								{space.name}
								{#if space.space_id === activeSpaceId}
									<svg class="ml-auto h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
										<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
									</svg>
								{/if}
							</button>
						{/each}
					</div>
				{/if}
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
	{@const widths = segmentWidths}
	{@const nonEmptySegments = segments.filter(s => s.entries.length > 0)}
	<div class="mt-3 rounded-lg border border-surface-700 bg-surface-800 px-4 py-3">
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
			{#if isViewingOlder}
				<button
					onclick={() => onVersionChange(latestVersion)}
					class="ml-2 rounded bg-laya-orange/15 px-2 py-0.5 text-[10px] font-medium text-laya-orange transition-colors hover:bg-laya-orange/25"
				>Now</button>
			{/if}
		</div>

		<!-- Segment labels — positioned at distorted midpoints so they shift with the fisheye -->
		<div class="relative h-4 mb-1 overflow-hidden">
			{#each segments as seg, si}
				{#if seg.entries.length > 0}
					{@const leftEdge = segmentEdges[si].left}
					{@const rightEdge = segmentEdges[si].right}
					{@const distLeft = mouseRatio !== null ? fisheye(leftEdge / 100, mouseRatio, 10) * 100 : leftEdge}
					{@const distRight = mouseRatio !== null ? fisheye(rightEdge / 100, mouseRatio, 10) * 100 : rightEdge}
					{@const center = (distLeft + distRight) / 2}
					<span
						class="absolute -translate-x-1/2 text-[9px] uppercase tracking-wider text-surface-500"
						style="left: {center}%; transition: left 150ms cubic-bezier(0.25, 0.1, 0.25, 1);"
					>{seg.label}</span>
				{/if}
			{/each}
		</div>

		<!-- Single-container fisheye track -->
		<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
		<div
			class="relative h-10 overflow-hidden cursor-pointer"
			bind:this={timelineEl}
			bind:clientWidth={timelineWidth}
			onmousemove={(e) => { handleTimelineMove(e); handleDotHover(e); }}
			onmouseleave={handleTimelineLeave}
			onclick={handleDotClick}
		>
			<!-- Track line — centered vertically in upper portion -->
			<div class="absolute left-0 right-0 top-[10px] h-px bg-surface-600"></div>

			<!-- Segment dividers -->
			{#each dividerPositions as dpos}
				<div
					class="absolute w-px h-5 bg-surface-600 top-[3px]"
					style="left: {mouseRatio !== null ? fisheye(dpos / 100, mouseRatio, 10) * 100 : dpos}%; transition: left 150ms cubic-bezier(0.25, 0.1, 0.25, 1);"
				></div>
			{/each}

			<!-- Hour ticks in Today segment — hang well below the track line -->
			{#each todayHourTicks as tick}
				{@const tickLeft = mouseRatio !== null ? fisheye(tick.globalPct / 100, mouseRatio, 10) * 100 : tick.globalPct}
				<div
					class="absolute flex flex-col items-center -translate-x-1/2 pointer-events-none top-[18px]"
					style="left: {tickLeft}%; transition: left 150ms cubic-bezier(0.25, 0.1, 0.25, 1);"
				>
					<div class="w-px h-2 bg-surface-600/40"></div>
					<span class="text-[7px] text-surface-600 mt-0.5 leading-none">{tick.label}</span>
				</div>
			{/each}

			<!-- All dots — centered on the track line -->
			{#each flatDots as dot}
				{@const isSelected = dot.entry.version === version}
				{@const isHovered = previewVersion !== null && dot.entry.version === previewVersion && !isSelected}
				{@const isActive = isSelected || isHovered}
				{@const leftPct = distortedPct(dot.segIndex, dot.localPct)}
				<div
					class="absolute -translate-x-1/2 -translate-y-1/2 flex items-center justify-center pointer-events-none top-[10px]"
					style="left: {leftPct}%; transition: left 150ms cubic-bezier(0.25, 0.1, 0.25, 1);"
				>
					<div class="p-1.5">
						<div class="rounded-full transition-colors
							{isActive ? 'w-2.5 h-2.5' : 'w-1.5 h-1.5'}
							{isSelected
								? 'bg-green-400'
								: isHovered
									? 'border-2 border-green-400 bg-transparent'
									: dot.isSynth
										? 'bg-laya-orange'
										: 'bg-surface-400'}
						"></div>
					</div>
				</div>
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
