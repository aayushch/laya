<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Calendar rail — meetings for the selected day, positioned by their REAL
	start/end (which live on the source event, never on a card; see
	GET /events/day). Overlapping meetings split the rail and turn red: a
	double-booking is the one calendar fact worth shouting about.
-->
<script lang="ts">
	import type { DayMeeting } from '$lib/api/types';
	import type { TimeScale } from '$lib/timeline/scale';
	import { formatMinutes, localMinutes } from '$lib/timeline/scale';
	import { layoutMeetings } from '$lib/timeline/threads';
	import { parseBackendDate } from '$lib/utils/datetime';

	let {
		meetings = [],
		scale,
		height,
		width = 132,
		stickyLeft = null,
		compact = false,
		nowMinute = null,
		onhover,
		onleave
	}: {
		meetings?: DayMeeting[];
		scale: TimeScale;
		/** Row height, which can exceed the scale's own (see contentHeight). */
		height: number;
		width?: number;
		/** Pins the rail this far from the left while expanded lanes scroll past it. */
		stickyLeft?: number | null;
		/** Narrow windows drop to time-only blocks (title on hover). */
		compact?: boolean;
		nowMinute?: number | null;
		onhover?: (el: HTMLElement, text: string) => void;
		onleave?: () => void;
	} = $props();

	function toMinutes(iso: string | null): number | null {
		if (!iso) return null;
		// Calendar payloads carry their own offset ("…T09:30:00-07:00"); Date
		// resolves that to the viewer's local clock, which is the axis here.
		const d = iso.includes('T') || iso.includes('+') ? new Date(iso) : parseBackendDate(iso);
		if (!d || Number.isNaN(d.getTime())) return null;
		return localMinutes(d);
	}

	const timed = $derived(
		meetings
			.filter((m) => !m.all_day)
			.map((m) => {
				const startMin = toMinutes(m.start);
				if (startMin === null) return null;
				const endMin = toMinutes(m.end);
				// A meeting with no end time still needs a body to be clickable.
				return { meeting: m, startMin, endMin: endMin !== null && endMin > startMin ? endMin : startMin + 30 };
			})
			.filter((m): m is { meeting: DayMeeting; startMin: number; endMin: number } => m !== null)
	);

	const blocks = $derived(layoutMeetings(timed));
	const allDay = $derived(meetings.filter((m) => m.all_day));

	function tooltipText(m: DayMeeting, startMin: number, endMin: number): string {
		const parts = [`${formatMinutes(startMin)}–${formatMinutes(endMin)}  ${m.title}`];
		if (m.location) parts.push(m.location);
		if (m.attendee_count > 0) parts.push(`${m.attendee_count} attendees`);
		if (m.cancelled) parts.push('Cancelled');
		return parts.join(' · ');
	}
</script>

<div
	class="tl-glass-surface {stickyLeft === null ? 'relative' : 'sticky z-10'} flex-none border-r"
	style="width: {width}px; border-color: var(--tl-divider); height: {height}px;
		{stickyLeft === null
			? 'background: var(--tl-rail-bg);'
			/* Pinned over scrolling lanes: layer the (translucent under glass) rail
			   tint on an opaque page base, or capsules would slide visibly through it. */
			: `left: ${stickyLeft}px; background: linear-gradient(var(--tl-rail-bg), var(--tl-rail-bg)), var(--color-surface-950);`}"
>
	<span class="absolute left-2 top-1.5 font-mono text-[8px] uppercase tracking-[0.1em]" style="color: var(--tl-micro)">Calendar</span>

	<!-- Hour gridlines are drawn in the rail as well as the lanes so meetings and
	     capsules read against the same grid. -->
	{#each scale.hourLines as line (line.minute)}
		<div class="absolute inset-x-0 h-px" style="top: {line.y}px; background: var(--tl-grid)"></div>
	{/each}

	{#if allDay.length > 0}
		<!-- All-day entries have no place on a clock axis, so they sit above it. -->
		<div class="absolute inset-x-1.5 top-6 flex flex-col gap-0.5">
			{#each allDay as m (m.event_id)}
				<div
					class="truncate rounded px-1.5 py-0.5 text-[8.5px] font-medium"
					style="background: var(--tl-meet-bg); color: var(--tl-meet-fg); border-left: 2px solid var(--tl-meet-edge);"
					title="All day · {m.title}"
				>
					{compact ? 'All day' : m.title}
				</div>
			{/each}
		</div>
	{/if}

	{#each blocks as block (block.meeting.event_id)}
		{@const clash = block.slots > 1}
		{@const top = scale.y(block.startMin)}
		{@const height = Math.max(22, scale.y(block.endMin) - top - 2)}
		{@const slotWidth = (width - 12) / block.slots}
		<div
			class="absolute overflow-hidden rounded px-[5px] py-[3px] leading-[1.25]"
			style="top: {top}px; height: {height}px; left: {6 + block.slot * slotWidth}px; width: {slotWidth - (block.slots > 1 ? 4 : 0)}px;
				background: {clash ? 'var(--tl-meet-clash-bg)' : 'var(--tl-meet-bg)'};
				border-left: 2px solid {clash ? 'var(--tl-meet-clash-edge)' : 'var(--tl-meet-edge)'};
				color: {clash ? 'var(--tl-meet-clash-fg)' : 'var(--tl-meet-fg)'};
				{block.meeting.cancelled ? 'opacity: 0.6;' : ''}"
			role="note"
			onmouseenter={(e) => onhover?.(e.currentTarget as HTMLElement, tooltipText(block.meeting, block.startMin, block.endMin))}
			onmouseleave={() => onleave?.()}
		>
			<div class="font-mono text-[8px] opacity-80">{formatMinutes(block.startMin)}</div>
			{#if !compact}
				<div class="truncate text-[9px] font-medium {block.meeting.cancelled ? 'line-through' : ''}">
					{block.meeting.title}
				</div>
			{/if}
		</div>
	{/each}

	{#if nowMinute !== null}
		<div class="pointer-events-none absolute inset-x-0 h-[1.5px] opacity-70" style="top: {scale.y(nowMinute)}px; background: var(--tl-now)"></div>
	{/if}
</div>
