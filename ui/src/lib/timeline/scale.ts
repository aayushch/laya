// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// The timeline's vertical axis. It is PIECEWISE, not linear over 24h: quiet
// stretches (only low-priority chatter, no open thread spanning them) collapse
// to a fixed-height hatched band so the hours that mattered get the pixels.
// Everything else is linear at `hourPx` per hour.
//
// Minutes are always minutes-from-LOCAL-midnight — the axis is the user's
// wall clock, so every conversion from a backend timestamp goes through
// `localMinutes()` below rather than reading UTC fields.

export interface QuietRun {
	startMin: number;
	endMin: number;
	/** Low-priority events that fall inside the run (shown in the band's label). */
	eventCount: number;
	/** Threads that span the run — open before it and still open after. */
	carriedThreads: number;
}

export interface ScaleOptions {
	domainStart: number;
	domainEnd: number;
	hourPx: number;
	/** Padding above the first row of content. */
	topPad?: number;
	/** Collapsed height of a quiet run. */
	quietBandPx?: number;
	/** Runs to collapse. Must be non-overlapping; they are sorted and clipped here. */
	collapsedRuns?: QuietRun[];
	/** Slack below the last minute so the final capsule isn't flush to the edge. */
	bottomPad?: number;
}

export interface TimeScale {
	domainStart: number;
	domainEnd: number;
	hourPx: number;
	topPad: number;
	quietBandPx: number;
	collapsedRuns: QuietRun[];
	/** Total pixel height of the scale. */
	height: number;
	/** minutes-from-midnight → px offset inside the lanes column. */
	y(minute: number): number;
	/** px offset → minutes-from-midnight (inverse of y, exact outside collapsed runs). */
	minuteAt(px: number): number;
	/** Whole hours in the domain that are NOT swallowed by a collapsed run. */
	hourLines: { minute: number; y: number }[];
	isCollapsed(minute: number): boolean;
}

export const DEFAULT_TOP_PAD = 26;
export const DEFAULT_QUIET_BAND_PX = 34;
export const DEFAULT_BOTTOM_PAD = 40;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Minutes from local midnight for a Date (the axis is wall-clock time). */
export function localMinutes(d: Date): number {
	return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

/** 'HH:MM' for a minutes-from-midnight value. */
export function formatMinutes(minute: number): string {
	const rounded = Math.round(minute);
	// End-of-day is 24:00, not 00:00 — a domain ending at midnight otherwise
	// labels its last gridline as if the day restarted there.
	if (rounded === 1440) return '24:00';
	const m = ((rounded % 1440) + 1440) % 1440;
	return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

export function buildScale(opts: ScaleOptions): TimeScale {
	const { domainStart, domainEnd, hourPx } = opts;
	const topPad = opts.topPad ?? DEFAULT_TOP_PAD;
	const quietBandPx = opts.quietBandPx ?? DEFAULT_QUIET_BAND_PX;
	const bottomPad = opts.bottomPad ?? DEFAULT_BOTTOM_PAD;

	// Clip to the domain, drop empties, sort, and merge any accidental overlap —
	// y() walks these in order and would double-count otherwise.
	const runs: QuietRun[] = [];
	for (const run of [...(opts.collapsedRuns ?? [])].sort((a, b) => a.startMin - b.startMin)) {
		const startMin = clamp(run.startMin, domainStart, domainEnd);
		const endMin = clamp(run.endMin, domainStart, domainEnd);
		if (endMin - startMin <= 0) continue;
		const prev = runs[runs.length - 1];
		if (prev && startMin <= prev.endMin) {
			prev.endMin = Math.max(prev.endMin, endMin);
			prev.eventCount += run.eventCount;
			prev.carriedThreads = Math.max(prev.carriedThreads, run.carriedThreads);
			continue;
		}
		runs.push({ ...run, startMin, endMin });
	}

	function y(minute: number): number {
		const m = clamp(minute, domainStart, domainEnd);
		let px = topPad;
		let cursor = domainStart;
		for (const run of runs) {
			if (m <= run.startMin) break;
			px += ((run.startMin - cursor) / 60) * hourPx;
			if (m < run.endMin) {
				// Inside the band: map proportionally so event dots still land in
				// time order within the collapsed strip.
				const span = run.endMin - run.startMin;
				return px + (span > 0 ? ((m - run.startMin) / span) * quietBandPx : 0);
			}
			px += quietBandPx;
			cursor = run.endMin;
		}
		return px + ((m - cursor) / 60) * hourPx;
	}

	function minuteAt(px: number): number {
		let remaining = px - topPad;
		let cursor = domainStart;
		if (remaining <= 0) return domainStart;
		for (const run of runs) {
			const linearPx = ((run.startMin - cursor) / 60) * hourPx;
			if (remaining < linearPx) return cursor + (remaining / hourPx) * 60;
			remaining -= linearPx;
			if (remaining < quietBandPx) {
				const span = run.endMin - run.startMin;
				return run.startMin + (quietBandPx > 0 ? (remaining / quietBandPx) * span : 0);
			}
			remaining -= quietBandPx;
			cursor = run.endMin;
		}
		return clamp(cursor + (remaining / hourPx) * 60, domainStart, domainEnd);
	}

	function isCollapsed(minute: number): boolean {
		return runs.some((r) => minute > r.startMin && minute < r.endMin);
	}

	const hourLines: { minute: number; y: number }[] = [];
	for (let m = Math.ceil(domainStart / 60) * 60; m <= domainEnd; m += 60) {
		if (isCollapsed(m)) continue;
		hourLines.push({ minute: m, y: y(m) });
	}

	return {
		domainStart,
		domainEnd,
		hourPx,
		topPad,
		quietBandPx,
		collapsedRuns: runs,
		height: y(domainEnd) + bottomPad,
		y,
		minuteAt,
		hourLines,
		isCollapsed
	};
}

export interface DomainOptions {
	/** Minutes that must be visible (event times, meeting starts/ends, now). */
	minutes: number[];
	/** Smallest domain span, so a one-event day isn't a 20px sliver. */
	minSpanMinutes?: number;
	/** Fallback window for an empty day. */
	fallback?: { start: number; end: number };
}

/**
 * Derive the visible time window from the day's own data, snapped to whole
 * hours with an hour of air on each side. The reference design's 06:00–20:00 is
 * just what this returns for the reference day.
 */
export function deriveDomain(opts: DomainOptions): { start: number; end: number } {
	const minSpan = opts.minSpanMinutes ?? 360;
	const fallback = opts.fallback ?? { start: 360, end: 1200 };
	const valid = opts.minutes.filter((m) => Number.isFinite(m));
	if (valid.length === 0) return { ...fallback };

	let start = Math.floor((Math.min(...valid) - 30) / 60) * 60;
	let end = Math.ceil((Math.max(...valid) + 30) / 60) * 60;
	start = clamp(start, 0, 1440);
	end = clamp(end, 0, 1440);

	if (end - start < minSpan) {
		end = Math.min(1440, start + minSpan);
		if (end - start < minSpan) start = Math.max(0, end - minSpan);
	}
	return { start, end };
}

export interface QuietDetectionInput {
	/** Spans of threads that count as "loud" — anything above LOW priority. */
	busySpans: { startMin: number; endMin: number }[];
	/** Spans of every thread, used to count what carries across a quiet run. */
	allSpans: { startMin: number; endMin: number }[];
	/** Minute of every card event on the day (for the band's event count). */
	eventMinutes: number[];
	domainStart: number;
	domainEnd: number;
	/** Runs shorter than this aren't worth collapsing. */
	minLengthMinutes?: number;
}

/**
 * A quiet run is a contiguous stretch with only low-priority events and no
 * above-LOW thread open across it. Returns them in chronological order.
 */
export function detectQuietRuns(input: QuietDetectionInput): QuietRun[] {
	const minLength = input.minLengthMinutes ?? 45;
	const { domainStart, domainEnd } = input;

	const merged: { startMin: number; endMin: number }[] = [];
	for (const span of [...input.busySpans].sort((a, b) => a.startMin - b.startMin)) {
		const startMin = clamp(span.startMin, domainStart, domainEnd);
		const endMin = clamp(span.endMin, domainStart, domainEnd);
		if (endMin < startMin) continue;
		const prev = merged[merged.length - 1];
		if (prev && startMin <= prev.endMin) prev.endMin = Math.max(prev.endMin, endMin);
		else merged.push({ startMin, endMin });
	}

	const runs: QuietRun[] = [];
	let cursor = domainStart;
	for (const span of [...merged, { startMin: domainEnd, endMin: domainEnd }]) {
		if (span.startMin - cursor >= minLength) {
			const startMin = cursor;
			const endMin = span.startMin;
			runs.push({
				startMin,
				endMin,
				eventCount: input.eventMinutes.filter((m) => m >= startMin && m < endMin).length,
				carriedThreads: input.allSpans.filter((s) => s.startMin < startMin && s.endMin > endMin).length
			});
		}
		cursor = Math.max(cursor, span.endMin);
	}
	return runs;
}
