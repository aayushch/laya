// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	monotonePath,
	monotoneAreaPath,
	niceStep,
	formatBucketLabel
} from './chartUtils';

type Pt = { x: number; y: number };

describe('monotonePath', () => {
	it('returns an empty string for fewer than two points', () => {
		expect(monotonePath([])).toBe('');
		expect(monotonePath([{ x: 0, y: 0 }])).toBe('');
	});

	it('draws a straight line (no cubic) for exactly two points', () => {
		// Coordinates are rounded to one decimal (the internal r() helper).
		expect(monotonePath([{ x: 0, y: 0 }, { x: 10, y: 20 }])).toBe('M0.0,0.0L10.0,20.0');
	});

	it('emits n-1 cubic segments through every point for three+ points', () => {
		const pts: Pt[] = [
			{ x: 0, y: 0 },
			{ x: 10, y: 20 },
			{ x: 20, y: 5 }
		];
		const d = monotonePath(pts);
		expect(d.startsWith('M0.0,0.0')).toBe(true);
		// Two segments for three points.
		expect(d.match(/ C/g)).toHaveLength(2);
		// The curve terminates exactly at the last data point.
		expect(d.endsWith('20.0,5.0')).toBe(true);
	});

	it('produces a valid path when a run of points is flat (zero slope branch)', () => {
		const d = monotonePath([
			{ x: 0, y: 5 },
			{ x: 10, y: 5 },
			{ x: 20, y: 5 }
		]);
		expect(d.startsWith('M0.0,5.0')).toBe(true);
		expect(d.endsWith('20.0,5.0')).toBe(true);
	});
});

describe('monotoneAreaPath', () => {
	it('returns an empty string for fewer than two points', () => {
		expect(monotoneAreaPath([], 100)).toBe('');
		expect(monotoneAreaPath([{ x: 0, y: 0 }], 100)).toBe('');
	});

	it('closes the curve down to the baseline and back', () => {
		const d = monotoneAreaPath([{ x: 0, y: 0 }, { x: 10, y: 20 }], 100);
		// Line segment, then down to baseline under the last x, across to the first
		// x at baseline, then Z.
		expect(d).toBe('M0.0,0.0L10.0,20.0 L10.0,100.0 L0.0,100.0 Z');
	});
});

describe('niceStep', () => {
	it('returns 1 for a non-positive max', () => {
		expect(niceStep(0, 5)).toBe(1);
		expect(niceStep(-42, 5)).toBe(1);
	});

	it('rounds the rough step up to 1/2/5/10 × power-of-ten', () => {
		expect(niceStep(50, 5)).toBe(10); // rough 10  → frac 1.0 → 1×10
		expect(niceStep(97, 5)).toBe(20); // rough 19.4 → frac 1.94 → 2×10
		expect(niceStep(35, 5)).toBe(5); //  rough 7   → frac 7 → 5×1
		expect(niceStep(40, 5)).toBe(10); // rough 8   → frac 8 → 10×1
	});

	it('honours the fraction boundaries (1.5 / 3 / 7)', () => {
		expect(niceStep(7.5, 5)).toBe(1); //  frac 1.5  → 1
		expect(niceStep(15, 5)).toBe(2); //   frac 3    → 2
		expect(niceStep(35, 5)).toBe(5); //   frac 7    → 5
	});

	it('scales the step to the magnitude of the data', () => {
		expect(niceStep(970, 5)).toBe(200); // rough 194  → frac 1.94 → 2×100
		expect(niceStep(1, 4)).toBeCloseTo(0.2); // rough 0.25 → frac 2.5 → 2×0.1
	});
});

describe('formatBucketLabel', () => {
	it('returns an empty string for an unparseable/empty timestamp', () => {
		expect(formatBucketLabel('', 60)).toBe('');
	});

	it('produces a non-empty label for each window tier', () => {
		// Locale/timezone-dependent output — assert shape, not exact text.
		const iso = '2026-07-01 14:30:00';
		expect(formatBucketLabel(iso, 60)).toMatch(/\d/); // <=360min: HH:MM
		expect(formatBucketLabel(iso, 1000)).toMatch(/\d/); // <=1440min: "Wkd HH:MM"
		expect(formatBucketLabel(iso, 5000)).toMatch(/\d/); // >1440min: "Mon D"
	});

	it('renders distinct formats for short vs multi-day windows', () => {
		const iso = '2026-07-01 14:30:00';
		// The multi-day label carries a month name; the intraday one does not.
		expect(formatBucketLabel(iso, 5000)).not.toBe(formatBucketLabel(iso, 60));
	});
});
