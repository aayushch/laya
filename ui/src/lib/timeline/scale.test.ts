// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	buildScale,
	deriveDomain,
	detectQuietRuns,
	formatMinutes,
	localMinutes,
	DEFAULT_TOP_PAD,
	DEFAULT_QUIET_BAND_PX
} from './scale';

describe('localMinutes / formatMinutes', () => {
	it('reads wall-clock minutes from a Date', () => {
		expect(localMinutes(new Date(2026, 4, 2, 9, 30))).toBe(570);
		expect(localMinutes(new Date(2026, 4, 2, 0, 0))).toBe(0);
	});
	it('formats minutes as zero-padded HH:MM', () => {
		expect(formatMinutes(570)).toBe('09:30');
		expect(formatMinutes(0)).toBe('00:00');
		expect(formatMinutes(1439)).toBe('23:59');
	});
	it('labels the end of the day 24:00, not 00:00', () => {
		expect(formatMinutes(1440)).toBe('24:00');
	});
});

describe('buildScale — linear region', () => {
	const scale = buildScale({ domainStart: 480, domainEnd: 1200, hourPx: 60 });

	it('places the domain start at the top padding', () => {
		expect(scale.y(480)).toBe(DEFAULT_TOP_PAD);
	});
	it('advances hourPx per hour', () => {
		expect(scale.y(540)).toBe(DEFAULT_TOP_PAD + 60);
		expect(scale.y(600)).toBe(DEFAULT_TOP_PAD + 120);
	});
	it('clamps outside the domain', () => {
		expect(scale.y(0)).toBe(scale.y(480));
		expect(scale.y(1439)).toBe(scale.y(1200));
	});
	it('round-trips through minuteAt', () => {
		for (const minute of [480, 555, 700, 1199]) {
			expect(scale.minuteAt(scale.y(minute))).toBeCloseTo(minute, 5);
		}
	});
	it('emits one gridline per whole hour in the domain', () => {
		expect(scale.hourLines.map((h) => h.minute)).toEqual([480, 540, 600, 660, 720, 780, 840, 900, 960, 1020, 1080, 1140, 1200]);
	});
	it('scales with zoom', () => {
		const zoomed = buildScale({ domainStart: 480, domainEnd: 1200, hourPx: 120 });
		expect(zoomed.y(540) - zoomed.y(480)).toBe(120);
	});
});

describe('buildScale — collapsed quiet runs', () => {
	// 06:00 → 20:00 with 06:00–08:30 collapsed, the reference design's shape.
	const scale = buildScale({
		domainStart: 360,
		domainEnd: 1200,
		hourPx: 60,
		collapsedRuns: [{ startMin: 360, endMin: 510, eventCount: 12, carriedThreads: 1 }]
	});

	it('replaces the run with the fixed band height', () => {
		expect(scale.y(510)).toBe(DEFAULT_TOP_PAD + DEFAULT_QUIET_BAND_PX);
	});
	it('keeps the linear scale below the band', () => {
		expect(scale.y(570)).toBe(DEFAULT_TOP_PAD + DEFAULT_QUIET_BAND_PX + 60);
	});
	it('maps minutes inside the run proportionally into the band', () => {
		const mid = scale.y(435); // halfway through 360–510
		expect(mid).toBeCloseTo(DEFAULT_TOP_PAD + DEFAULT_QUIET_BAND_PX / 2, 5);
	});
	it('round-trips inside and outside the band', () => {
		for (const minute of [400, 510, 700, 1100]) {
			expect(scale.minuteAt(scale.y(minute))).toBeCloseTo(minute, 5);
		}
	});
	it('drops gridlines swallowed by the band', () => {
		expect(scale.hourLines.map((h) => h.minute)).not.toContain(420); // 07:00
		expect(scale.hourLines.map((h) => h.minute)).toContain(540); // 09:00
	});
	it('reports collapsed minutes', () => {
		expect(scale.isCollapsed(400)).toBe(true);
		expect(scale.isCollapsed(600)).toBe(false);
	});
	it('merges overlapping runs instead of double-counting their height', () => {
		const merged = buildScale({
			domainStart: 360,
			domainEnd: 1200,
			hourPx: 60,
			collapsedRuns: [
				{ startMin: 360, endMin: 510, eventCount: 4, carriedThreads: 0 },
				{ startMin: 480, endMin: 600, eventCount: 3, carriedThreads: 1 }
			]
		});
		expect(merged.collapsedRuns).toHaveLength(1);
		expect(merged.y(600)).toBe(DEFAULT_TOP_PAD + DEFAULT_QUIET_BAND_PX);
	});
	it('is shorter than the same domain drawn linearly', () => {
		const linear = buildScale({ domainStart: 360, domainEnd: 1200, hourPx: 60 });
		expect(scale.height).toBeLessThan(linear.height);
	});
});

describe('deriveDomain', () => {
	it('snaps to whole hours with air on both sides', () => {
		expect(deriveDomain({ minutes: [545, 1010] })).toEqual({ start: 480, end: 1080 });
	});
	it('falls back to a working-day window when there is no data', () => {
		expect(deriveDomain({ minutes: [] })).toEqual({ start: 360, end: 1200 });
	});
	it('enforces a minimum span for a single-event day', () => {
		const { start, end } = deriveDomain({ minutes: [600] });
		expect(end - start).toBeGreaterThanOrEqual(360);
		expect(start).toBeLessThanOrEqual(600);
		expect(end).toBeGreaterThanOrEqual(600);
	});
	it('never leaves the 24h day', () => {
		const { start, end } = deriveDomain({ minutes: [5, 1435] });
		expect(start).toBe(0);
		expect(end).toBe(1440);
	});
});

describe('detectQuietRuns', () => {
	it('finds the leading quiet stretch before the first loud thread', () => {
		const runs = detectQuietRuns({
			busySpans: [{ startMin: 510, endMin: 900 }],
			allSpans: [{ startMin: 510, endMin: 900 }],
			eventMinutes: [370, 400, 480],
			domainStart: 360,
			domainEnd: 1200
		});
		expect(runs[0]).toMatchObject({ startMin: 360, endMin: 510, eventCount: 3 });
	});
	it('ignores gaps shorter than the minimum', () => {
		const runs = detectQuietRuns({
			busySpans: [
				{ startMin: 360, endMin: 600 },
				{ startMin: 620, endMin: 900 }
			],
			allSpans: [],
			eventMinutes: [],
			domainStart: 360,
			domainEnd: 900
		});
		expect(runs).toEqual([]);
	});
	it('counts threads carried across a run', () => {
		const runs = detectQuietRuns({
			busySpans: [{ startMin: 700, endMin: 900 }],
			allSpans: [{ startMin: 300, endMin: 1000 }],
			eventMinutes: [],
			domainStart: 360,
			domainEnd: 1000
		});
		expect(runs[0].carriedThreads).toBe(1);
	});
	it('merges overlapping busy spans before looking for gaps', () => {
		const runs = detectQuietRuns({
			busySpans: [
				{ startMin: 400, endMin: 700 },
				{ startMin: 500, endMin: 900 }
			],
			allSpans: [],
			eventMinutes: [],
			domainStart: 360,
			domainEnd: 1200
		});
		expect(runs.map((r) => [r.startMin, r.endMin])).toEqual([[900, 1200]]);
	});
	it('returns nothing when the day is busy throughout', () => {
		expect(
			detectQuietRuns({
				busySpans: [{ startMin: 360, endMin: 1200 }],
				allSpans: [],
				eventMinutes: [],
				domainStart: 360,
				domainEnd: 1200
			})
		).toEqual([]);
	});
});
