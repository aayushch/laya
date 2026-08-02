<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Pulse → Timeline ("Day Column"): the selected day as a clock-time column.

	Four columns: hour gutter (also the time-range brush), calendar rail,
	thread lanes, and the pinned heat rail. The vertical axis is piecewise —
	quiet stretches collapse into a hatched band so the hours that mattered get
	the pixels (see lib/timeline/scale.ts).

	It renders the SAME CardGroups the card/list views render and opens the same
	detail panel, so nothing here forks the feed's data or selection model.
-->
<script lang="ts">
	import { tick, untrack } from 'svelte';
	import type { ActionCard, CardGroup, DayEventsResponse } from '$lib/api/types';
	import { buildThreads, attentionMarks, type Thread } from '$lib/timeline/threads';
	import {
		buildScale,
		deriveDomain,
		detectQuietRuns,
		formatMinutes,
		localMinutes,
		type QuietRun
	} from '$lib/timeline/scale';
	import { packLanes, laneGeometry, lanesForWidth } from '$lib/timeline/lanes';
	import { timelineView } from '$lib/stores/timelineView';
	import { feedFilters } from '$lib/stores/feedFilters';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { portal } from '$lib/actions/portal';
	import { platformDotColor, platformLabel } from '$lib/utils/cardVisuals';
	import TimelineControls from './TimelineControls.svelte';
	import CalendarRail from './CalendarRail.svelte';
	import ThreadCapsule from './ThreadCapsule.svelte';
	import OverflowStrip from './OverflowStrip.svelte';
	import HeatRail from './HeatRail.svelte';

	let {
		groups = [],
		dayEvents = null,
		loading = false,
		date,
		isToday = false,
		selectedCardId = '',
		selectedEntityId = '',
		hasAnySelection = false,
		hasMore = false,
		loadingMore = false,
		remaining = 0,
		onloadmore,
		onselectcard,
		onselectgroup,
		emptyLabel = 'No cards for this day'
	}: {
		groups?: CardGroup[];
		dayEvents?: DayEventsResponse | null;
		loading?: boolean;
		date: string;
		isToday?: boolean;
		selectedCardId?: string;
		selectedEntityId?: string;
		hasAnySelection?: boolean;
		hasMore?: boolean;
		loadingMore?: boolean;
		remaining?: number;
		onloadmore?: () => void;
		onselectcard: (card: ActionCard) => void;
		onselectgroup: (group: CardGroup) => void;
		emptyLabel?: string;
	} = $props();

	const GUTTER_W = 56;
	const RAIL_W = 132;
	const RAIL_W_COMPACT = 72;
	const HEAT_W = 46;
	const HEAT_W_SLIVER = 12;
	const STRIP_W = 56;
	/** Enough lanes for a heavy day; the expanded area scrolls sideways to reach them. */
	const MAX_EXPANDED_LANES = 40;
	/** Lane width in expanded mode — fits an entity key plus readable subject text. */
	const EXPANDED_LANE_PX = 132;

	// ── clock ───────────────────────────────────────────────────────────
	// One shared ticker: the now line, the "jump to now" label and the ageing
	// rules in threadAttention all read it, so they can't drift apart.
	let now = $state(new Date());
	$effect(() => {
		const id = setInterval(() => (now = new Date()), 60_000);
		return () => clearInterval(id);
	});
	const nowMinute = $derived(localMinutes(now));

	// ── data → geometry ─────────────────────────────────────────────────
	const threads = $derived(buildThreads(groups, { date, now }));

	const meetingMinutes = $derived.by(() => {
		const out: number[] = [];
		for (const m of dayEvents?.meetings ?? []) {
			if (m.all_day || !m.start) continue;
			const d = new Date(m.start);
			if (!Number.isNaN(d.getTime())) out.push(localMinutes(d));
		}
		return out;
	});

	const domain = $derived(
		deriveDomain({
			minutes: [
				...threads.flatMap((t) => [t.firstMinute, t.lastMinute]),
				...meetingMinutes,
				...(isToday ? [nowMinute] : [])
			]
		})
	);

	const eventMinutes = $derived(
		threads.flatMap((t) => t.events.filter((e) => !e.carried).map((e) => e.minute))
	);

	const quietRuns = $derived(
		detectQuietRuns({
			busySpans: threads
				.filter((t) => t.priority !== 'LOW')
				.map((t) => ({ startMin: t.firstMinute, endMin: t.lastMinute })),
			allSpans: threads.map((t) => ({ startMin: t.firstMinute, endMin: t.lastMinute })),
			eventMinutes,
			domainStart: domain.start,
			domainEnd: domain.end
		})
	);

	// Per-run expansion. The store's quietCollapsed is the DEFAULT for runs the
	// user hasn't touched; clicking a band overrides it for that band only.
	let expandedRuns = $state(new Set<string>());
	let collapsedRuns = $state(new Set<string>());
	const runKey = (r: QuietRun) => `${r.startMin}-${r.endMin}`;
	// A new day is a new set of runs — stale keys would silently expand bands.
	$effect(() => {
		date;
		expandedRuns = new Set();
		collapsedRuns = new Set();
	});

	function isRunCollapsed(run: QuietRun): boolean {
		const key = runKey(run);
		if (expandedRuns.has(key)) return false;
		if (collapsedRuns.has(key)) return true;
		return $timelineView.quietCollapsed;
	}

	function toggleRun(run: QuietRun) {
		const key = runKey(run);
		const next = { expanded: new Set(expandedRuns), collapsed: new Set(collapsedRuns) };
		if (isRunCollapsed(run)) {
			next.expanded.add(key);
			next.collapsed.delete(key);
		} else {
			next.collapsed.add(key);
			next.expanded.delete(key);
		}
		expandedRuns = next.expanded;
		collapsedRuns = next.collapsed;
	}

	const scale = $derived(
		buildScale({
			domainStart: domain.start,
			domainEnd: domain.end,
			hourPx: $timelineView.hourPx,
			collapsedRuns: quietRuns.filter(isRunCollapsed)
		})
	);

	// ── lanes ───────────────────────────────────────────────────────────
	// The lanes' AVAILABLE width is derived from the scroll viewport rather than
	// measured on the lanes element: in expanded mode that element is deliberately
	// wider than the viewport, so measuring it would feed its own width back in.
	let scrollViewportWidth = $state(1400);
	let bodyWidth = $state(1400);

	const heatWidth = $derived(bodyWidth < 900 ? HEAT_W_SLIVER : HEAT_W);
	const railWidth = $derived(bodyWidth < 1000 ? RAIL_W_COMPACT : RAIL_W);
	const lanesWidth = $derived(Math.max(240, scrollViewportWidth - GUTTER_W - railWidth));
	const baseLanes = $derived(lanesForWidth(lanesWidth, $timelineView.laneCount));

	const laneInputs = $derived(
		threads.map((t) => ({
			key: t.key,
			startMin: t.firstMinute,
			endMin: t.lastMinute,
			priority: t.priority,
			data: t
		}))
	);

	// First pass with the normal lane count tells us how much actually overflows;
	// expanding widens the lanes to absorb it (capped, so capsules stay readable).
	const basePack = $derived(
		packLanes(laneInputs, {
			lanes: baseLanes,
			y: scale.y,
			clampStart: domain.start
		})
	);

	// Expanding does NOT squeeze the lanes — a lane too narrow for an entity key
	// and a subject line shows nothing but a priority badge, which is worse than
	// the overflow strip it replaced. Instead the lanes area grows past the
	// viewport at a fixed readable width and scrolls sideways, so every
	// overflowed thread is reachable at full fidelity.
	const effectiveLanes = $derived(
		$timelineView.overflowExpanded
			? Math.min(MAX_EXPANDED_LANES, baseLanes + basePack.overflow.length)
			: baseLanes
	);

	const pack = $derived(
		$timelineView.overflowExpanded
			? packLanes(laneInputs, { lanes: effectiveLanes, y: scale.y, clampStart: domain.start })
			: basePack
	);

	const stripWidth = $derived(pack.overflow.length > 0 && !$timelineView.overflowExpanded ? STRIP_W : 0);
	const lanesContentWidth = $derived(
		$timelineView.overflowExpanded
			? Math.max(lanesWidth, effectiveLanes * EXPANDED_LANE_PX)
			: lanesWidth
	);
	const geo = $derived(laneGeometry(lanesContentWidth, effectiveLanes, stripWidth));

	// A capsule starting near the end of the day still gets the 98px minimum, so
	// it can reach past the scale's own height — the column has to grow to it or
	// the last thread of the day is clipped by the scroll container.
	const contentHeight = $derived(
		Math.max(scale.height, ...pack.placed.map((p) => p.top + p.height + 16))
	);

	const marks = $derived(attentionMarks(threads));

	// Space marks only appear when they can actually tell you something: with a
	// single space on screen every capsule would carry an identical stripe, which
	// is noise. Same rule as the overflow strip only appearing once lanes run out.
	const multiSpace = $derived(new Set(threads.map((t) => t.spaceId ?? 'default')).size > 1);

	const sourcePlatforms = $derived.by(() => {
		const counts = new Map<string, number>(Object.entries(dayEvents?.platforms ?? {}));
		// A platform with cards but no counted events (space mismatch, older data)
		// must still get a chip, or it can't be filtered.
		for (const t of threads) {
			if (t.platform && !counts.has(t.platform)) counts.set(t.platform, 0);
		}
		return [...counts.entries()]
			.map(([key, count]) => ({ key, count }))
			.sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
	});

	// ── scrolling ───────────────────────────────────────────────────────
	let scrollEl: HTMLElement | undefined = $state();
	let viewportHeight = $state(0);
	let scrollTop = $state(0);
	// Anchor minute at the viewport's centre, captured with the CURRENT scale on
	// every scroll — the zoom effect below reads it after the scale has already
	// changed, so it must not be derived from the new scale.
	let anchorMinute = 0;

	function handleScroll() {
		if (!scrollEl) return;
		scrollTop = scrollEl.scrollTop;
		anchorMinute = scale.minuteAt(scrollTop + viewportHeight / 2);
	}

	const viewportRange = $derived({
		from: scale.minuteAt(scrollTop),
		to: scale.minuteAt(scrollTop + viewportHeight)
	});

	// Preserve the scroll anchor across zoom steps: the minute at the centre of
	// the viewport stays put, so zooming doesn't teleport you across the day.
	let lastHourPx = $timelineView.hourPx;
	$effect(() => {
		const hourPx = $timelineView.hourPx;
		untrack(() => {
			// Skip the mount run — the anchor is still 0 there and it would fight
			// the initial jump-to-now below.
			if (hourPx === lastHourPx) return;
			lastHourPx = hourPx;
			if (!scrollEl || viewportHeight === 0) return;
			const target = anchorMinute;
			tick().then(() => {
				if (!scrollEl) return;
				scrollEl.scrollTop = Math.max(0, scale.y(target) - viewportHeight / 2);
				scrollTop = scrollEl.scrollTop;
			});
		});
	});

	function scrollToMinute(minute: number, position: 'third' | 'center' = 'third') {
		if (!scrollEl) return;
		const offset = position === 'third' ? viewportHeight / 3 : viewportHeight / 2;
		// Never scrollIntoView here: it walks every scrollable ancestor and drags
		// the whole feed page with it.
		scrollEl.scrollTo({
			top: Math.max(0, scale.y(minute) - offset),
			behavior: $reducedMotion ? 'auto' : 'smooth'
		});
	}

	// Land on "now" the first time today's timeline renders — opening the view at
	// 06:00 when it's 4pm makes it look empty.
	let jumpedForDate = $state('');
	$effect(() => {
		if (loading || threads.length === 0 || jumpedForDate === date) return;
		jumpedForDate = date;
		if (isToday) {
			tick().then(() => scrollToMinute(untrack(() => nowMinute)));
		}
	});

	// ── time-range brush (drag in the hour gutter) ──────────────────────
	let gutterEl: HTMLElement | undefined = $state();
	let dragFrom = $state<number | null>(null);
	let dragTo = $state<number | null>(null);
	const dragging = $derived(dragFrom !== null && dragTo !== null);
	const brush = $derived($feedFilters.timeBrush);

	function minuteFromPointer(e: PointerEvent): number {
		if (!gutterEl) return domain.start;
		const rect = gutterEl.getBoundingClientRect();
		return scale.minuteAt(e.clientY - rect.top);
	}

	function startBrush(e: PointerEvent) {
		if (e.button !== 0) return;
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		dragFrom = minuteFromPointer(e);
		dragTo = dragFrom;
	}

	function moveBrush(e: PointerEvent) {
		if (dragFrom === null) return;
		dragTo = minuteFromPointer(e);
	}

	function endBrush(e: PointerEvent) {
		if (dragFrom === null || dragTo === null) {
			dragFrom = dragTo = null;
			return;
		}
		const from = Math.round(Math.min(dragFrom, dragTo));
		const to = Math.round(Math.max(dragFrom, dragTo));
		dragFrom = dragTo = null;
		(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
		// A click (rather than a drag) clears an active brush instead of setting a
		// zero-width one.
		$feedFilters.timeBrush = to - from < 5 ? null : { from, to };
	}

	const brushBand = $derived.by(() => {
		const from = dragging ? Math.min(dragFrom!, dragTo!) : brush?.from;
		const to = dragging ? Math.max(dragFrom!, dragTo!) : brush?.to;
		if (from === undefined || to === undefined || from === null || to === null) return null;
		const top = scale.y(from);
		return { top, height: Math.max(2, scale.y(to) - top), from, to };
	});

	// ── tooltips ────────────────────────────────────────────────────────
	// Portalled to <body>: capsules live inside backdrop-filter ancestors under
	// the glass theme, which trap position:fixed children — an inline tooltip
	// gets painted over by later capsules.
	interface TooltipState {
		top: number;
		left: number;
		thread?: Thread;
		cardId?: string;
		text?: string;
	}
	let tooltip = $state<TooltipState | null>(null);
	let dwellTimer: ReturnType<typeof setTimeout> | null = null;
	const DWELL_MS = 250;

	function place(el: HTMLElement): { top: number; left: number } {
		const r = el.getBoundingClientRect();
		const width = 300;
		const left = r.right + width + 16 < window.innerWidth ? r.right + 8 : Math.max(8, r.left - width - 8);
		return { top: Math.min(Math.max(8, r.top), window.innerHeight - 160), left };
	}

	function showThreadTooltip(el: HTMLElement, thread: Thread, cardId?: string) {
		if (dwellTimer) clearTimeout(dwellTimer);
		dwellTimer = setTimeout(() => {
			tooltip = { ...place(el), thread, cardId };
		}, DWELL_MS);
	}

	function showTextTooltip(el: HTMLElement, text: string) {
		if (dwellTimer) clearTimeout(dwellTimer);
		dwellTimer = setTimeout(() => {
			tooltip = { ...place(el), text };
		}, DWELL_MS);
	}

	function hideTooltip() {
		if (dwellTimer) clearTimeout(dwellTimer);
		dwellTimer = null;
		tooltip = null;
	}

	const tooltipCard = $derived.by(() => {
		if (!tooltip?.thread) return null;
		const t = tooltip.thread;
		return tooltip.cardId ? (t.events.find((e) => e.cardId === tooltip!.cardId)?.card ?? t.latest.card) : t.latest.card;
	});

	// ── selection ───────────────────────────────────────────────────────
	function selectThread(thread: Thread) {
		hideTooltip();
		if (thread.singleCard) onselectcard(thread.group.cards[0]);
		else onselectgroup(thread.group);
	}

	function selectEvent(thread: Thread, cardId: string) {
		hideTooltip();
		const card = thread.group.cards.find((c) => c.card_id === cardId);
		if (card) onselectcard(card);
	}
</script>

<div class="flex h-full min-h-0 flex-col" bind:clientWidth={bodyWidth}>
	<TimelineControls
		{sourcePlatforms}
		{isToday}
		nowMinute={nowMinute}
		{hasMore}
		{loadingMore}
		{remaining}
		{onloadmore}
		onjumptonow={() => scrollToMinute(nowMinute)}
	/>

	<div class="flex min-h-0 flex-1">
		<!-- Scrolling part: gutter + calendar rail + lanes -->
		<div
			bind:this={scrollEl}
			bind:clientHeight={viewportHeight}
			bind:clientWidth={scrollViewportWidth}
			onscroll={handleScroll}
			class="relative min-w-0 flex-1 overflow-y-auto {$timelineView.overflowExpanded ? 'overflow-x-auto' : 'overflow-x-hidden'}"
			style="background: var(--color-surface-950)"
		>
			<div class="flex {$timelineView.overflowExpanded ? 'w-max' : ''}" style="height: {contentHeight}px">
				<!-- Hour gutter — also the drag surface for the time-range brush.
				     Sticky so the clock stays put while expanded lanes scroll past it. -->
				<div
					bind:this={gutterEl}
					class="sticky left-0 z-20 flex-none cursor-ns-resize select-none border-r"
					style="width: {GUTTER_W}px; border-color: var(--tl-divider); background: var(--color-surface-950)"
					onpointerdown={startBrush}
					onpointermove={moveBrush}
					onpointerup={endBrush}
					onpointercancel={endBrush}
					role="presentation"
					title="Drag to filter the feed to a time range"
				>
					{#each scale.hourLines as line (line.minute)}
						<div class="absolute right-1.5 font-mono text-[10px] leading-none" style="top: {line.y - 4}px; color: var(--color-surface-400)">
							{formatMinutes(line.minute)}
						</div>
					{/each}
					{#if brushBand}
						<div
							class="pointer-events-none absolute inset-x-0"
							style="top: {brushBand.top}px; height: {brushBand.height}px; background: var(--tl-brush); border-top: 1px solid var(--tl-brush-edge); border-bottom: 1px solid var(--tl-brush-edge);"
						></div>
					{/if}
				</div>

				<CalendarRail
					meetings={dayEvents?.meetings ?? []}
					{scale}
					height={contentHeight}
					width={railWidth}
					stickyLeft={$timelineView.overflowExpanded ? GUTTER_W : null}
					compact={railWidth === RAIL_W_COMPACT}
					nowMinute={isToday ? nowMinute : null}
					onhover={showTextTooltip}
					onleave={hideTooltip}
				/>

				<!-- Lanes -->
				<div class="relative flex-none" style="width: {lanesContentWidth}px">
					{#each scale.hourLines as line (line.minute)}
						<div class="absolute inset-x-0 h-px" style="top: {line.y}px; background: var(--tl-grid)"></div>
					{/each}

					{#if brushBand}
						<div
							class="pointer-events-none absolute inset-x-0"
							style="top: {brushBand.top}px; height: {brushBand.height}px; background: var(--tl-brush);"
						></div>
					{/if}

					<!-- Quiet bands: collapsed stretches of low-priority noise -->
					{#each quietRuns as run (runKey(run))}
						{@const collapsed = isRunCollapsed(run)}
						<button
							class="absolute inset-x-0 flex items-center gap-2.5 px-3 text-left {collapsed ? 'tl-quiet-band' : 'border-b border-dashed'}"
							style="top: {scale.y(run.startMin)}px; height: {collapsed
								? scale.quietBandPx
								: scale.y(run.endMin) - scale.y(run.startMin)}px; {collapsed ? '' : 'border-color: var(--tl-quiet-border); align-items: flex-start; padding-top: 4px;'}"
							onclick={() => toggleRun(run)}
							title={collapsed ? 'Expand this quiet stretch' : 'Collapse this quiet stretch'}
						>
							<span class="shrink-0 font-mono text-[9px] uppercase tracking-[0.1em]" style="color: var(--tl-quiet-label)">
								{formatMinutes(run.startMin)} – {formatMinutes(run.endMin)} Quiet
							</span>
							<span class="truncate text-[10px]" style="color: var(--tl-quiet-text)">
								{run.eventCount} low-priority {run.eventCount === 1 ? 'event' : 'events'}{run.carriedThreads > 0
									? `, ${run.carriedThreads} thread${run.carriedThreads === 1 ? '' : 's'} carried forward`
									: ''} — click to {collapsed ? 'expand' : 'collapse'}
							</span>
						</button>
					{/each}

					{#if loading && threads.length === 0}
						<!-- Skeleton capsules: the grid is already drawn above, so only the
						     content needs a placeholder. -->
						{#each [0, 1, 2] as lane}
							{#each [0, 1] as row}
								<div
									class="absolute animate-pulse rounded-[7px]"
									style="left: {geo.left(lane)}px; width: {geo.width}px; top: {scale.topPad + row * 150 + 20}px; height: 98px; background: var(--tl-capsule-bg); border: 1px solid var(--tl-capsule-border);"
								></div>
							{/each}
						{/each}
					{:else if threads.length === 0}
						<div class="absolute inset-x-0 top-24 flex flex-col items-center justify-center text-center text-surface-500">
							<p class="text-laya-heading">{emptyLabel}</p>
							<p class="mt-1 text-laya-base">Cards will appear here as events are processed</p>
						</div>
					{:else}
						{#each pack.placed as item (item.key)}
							<ThreadCapsule
								thread={item.data}
								top={item.top}
								height={item.height}
								left={geo.left(item.lane)}
								width={geo.width}
								showSpace={multiSpace}
								selected={item.data.entityId === selectedEntityId ||
									item.data.events.some((e) => e.cardId === selectedCardId)}
								dimmed={hasAnySelection &&
									item.data.entityId !== selectedEntityId &&
									!item.data.events.some((e) => e.cardId === selectedCardId)}
								{selectedCardId}
								dotY={scale.y}
								onselect={selectThread}
								onselectcard={selectEvent}
								onhover={showThreadTooltip}
								onleave={hideTooltip}
							/>
						{/each}

						{#if stripWidth > 0}
							<OverflowStrip
								threads={pack.overflow.map((o) => ({
									thread: o.data,
									startMin: o.startMin,
									endMin: o.endMin
								}))}
								{scale}
								width={STRIP_W - 6}
								showSpace={multiSpace}
								onexpand={() => timelineView.setOverflowExpanded(true)}
								onhover={showTextTooltip}
								onleave={hideTooltip}
							/>
						{/if}
					{/if}

					{#if isToday && nowMinute >= domain.start && nowMinute <= domain.end}
						<div class="pointer-events-none absolute inset-x-0 z-10 h-[1.5px]" style="top: {scale.y(nowMinute)}px; background: var(--tl-now)">
							<span
								class="absolute left-0 rounded-full px-1.5 py-px font-mono text-[9px] font-semibold"
								style="top: -8px; background: var(--tl-now); color: var(--tl-now-fg)"
							>{formatMinutes(nowMinute)} NOW</span>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Pinned rails -->
		<div class="relative flex flex-none">
			{#if $timelineView.overflowExpanded && basePack.overflow.length > 0}
				<button
					class="absolute -left-24 top-1 z-20 rounded-md border px-2 py-0.5 text-[10px] font-medium"
					style="border-color: var(--tl-control-border); background: var(--tl-control-bg); color: var(--color-surface-300)"
					onclick={() => timelineView.setOverflowExpanded(false)}
				>Collapse lanes</button>
			{/if}
			<HeatRail
				buckets={dayEvents?.buckets ?? []}
				bucketMinutes={dayEvents?.bucket_minutes ?? 30}
				{marks}
				domainStart={domain.start}
				domainEnd={domain.end}
				viewport={viewportRange}
				width={heatWidth}
				showSpace={multiSpace}
				onseek={(minute) => scrollToMinute(minute, 'center')}
				onhover={showTextTooltip}
				onleave={hideTooltip}
			/>
		</div>
	</div>
</div>

{#if tooltip}
	<!-- Portalled to <body>, so backdrop-filter reaches the real page: this wants
	     the translucent .glass-tooltip, not the near-opaque -dense variant (which
	     exists for tooltips trapped inside a glass container that can't blur
	     through to content). The class carries its own border under glass. -->
	<div
		use:portal
		class="pointer-events-none fixed z-[100] w-[300px] rounded-lg px-3 py-2 glass-tooltip"
		style="top: {tooltip.top}px; left: {tooltip.left}px"
	>
		{#if tooltip.thread && tooltipCard}
			{@const thread = tooltip.thread}
			<div class="flex items-center gap-1.5 text-[10px]">
				<span class="h-1.5 w-1.5 rounded-full" style="background-color: {platformDotColor(thread.platform)}"></span>
				<span class="font-mono font-semibold">{platformLabel(thread.platform)}</span>
				<span class="opacity-60">·</span>
				<span class="min-w-0 truncate font-mono">{thread.title}</span>
				<span class="ml-auto shrink-0 font-mono opacity-70">
					{formatMinutes(thread.firstMinute)}–{formatMinutes(thread.lastMinute)}
				</span>
			</div>
			<div class="mt-1.5 text-[11px] font-semibold leading-snug">{tooltipCard.header}</div>
			{#if tooltipCard.summary}
				<div class="mt-1 line-clamp-3 text-[10.5px] leading-relaxed opacity-80">{tooltipCard.summary}</div>
			{/if}
			<div class="mt-2 flex flex-wrap items-center gap-1.5 text-[9px] font-medium">
				<span class="rounded px-1.5 py-0.5" style="background: var(--tl-bg-dormant); color: var(--tl-fg-dormant)">{tooltipCard.persona}</span>
				<span class="rounded px-1.5 py-0.5" style="background: var(--tl-bg-ready); color: var(--tl-fg-ready)">{thread.latest.statusLabel}</span>
				{#if thread.spaceName}
					<!-- Named here rather than on the capsule: the stripe carries the
					     colour, the tooltip is where it gets a word. -->
					<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5" style="background: var(--tl-bg-dormant); color: var(--tl-fg-dormant)">
						<span class="h-1.5 w-1.5 rounded-full" style="background: {thread.spaceColor ?? 'var(--color-laya-orange)'}"></span>
						{thread.spaceName}
					</span>
				{/if}
				<span class="font-mono opacity-70">{thread.cardCount} events · {thread.openHours.toFixed(1)}h</span>
			</div>
			{#if thread.attention.reason}
				<div class="mt-1.5 text-[9.5px]" style="color: var(--tl-fg-failed)">{thread.attention.reason}</div>
			{/if}
		{:else if tooltip.text}
			<div class="text-[10.5px] leading-snug">{tooltip.text}</div>
		{/if}
	</div>
{/if}
