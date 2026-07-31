// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	LAYERS,
	countdownTo,
	duration,
	layerLabel,
	livePriority,
	priorityToken,
	shortAge
} from './layers';

describe('LAYERS', () => {
	it('is the compression chain in order', () => {
		expect(LAYERS.map((l) => l.type)).toEqual([
			'attention',
			'recent',
			'period',
			'milestone'
		]);
	});

	it('narrows monotonically — the funnel shape is the message', () => {
		const widths = LAYERS.map((l) => parseInt(l.width, 10));
		expect(widths).toEqual([100, 89, 75, 60]);
		for (let i = 1; i < widths.length; i++) {
			expect(widths[i]).toBeLessThan(widths[i - 1]);
		}
	});
});

describe('layerLabel', () => {
	it('uppercases the display title, not the raw type', () => {
		expect(layerLabel('period')).toBe('THIS WEEK');
		expect(layerLabel('attention')).toBe('NEEDS ATTENTION');
	});

	it('is empty for a missing section', () => {
		expect(layerLabel(null)).toBe('');
		expect(layerLabel(undefined)).toBe('');
	});

	it('passes unknown types through', () => {
		expect(layerLabel('mystery')).toBe('MYSTERY');
	});
});

describe('priorityToken', () => {
	it('lowercases known priorities', () => {
		expect(priorityToken('CRITICAL')).toBe('critical');
		expect(priorityToken('LOW')).toBe('low');
	});

	it('defaults unknown or missing values to medium', () => {
		expect(priorityToken(undefined)).toBe('medium');
		expect(priorityToken('WEIRD')).toBe('medium');
	});
});

describe('livePriority', () => {
	it('prefers the live value over the frozen one', () => {
		// Frozen CRITICAL, but every open card is now only MEDIUM
		expect(livePriority({ priority: 'CRITICAL', live: { max_priority: 'MEDIUM' } })).toBe(
			'MEDIUM'
		);
	});

	it('falls back to the frozen value when nothing is live', () => {
		expect(livePriority({ priority: 'HIGH', live: undefined })).toBe('HIGH');
	});

	it('falls back when every source card has resolved', () => {
		// max_priority null means "all resolved" — the frozen label is all we have
		expect(livePriority({ priority: 'HIGH', live: { max_priority: null } })).toBe('HIGH');
	});
});

describe('shortAge', () => {
	const now = new Date('2026-05-04T18:00:00Z').getTime();
	const iso = (s: string) => s;

	it('reports minutes under an hour', () => {
		expect(shortAge(iso('2026-05-04 17:40:00'), now)).toBe('20m');
	});

	it('reports hours up to two days', () => {
		expect(shortAge(iso('2026-05-04 12:00:00'), now)).toBe('6h');
		expect(shortAge(iso('2026-05-03 20:00:00'), now)).toBe('22h');
	});

	it('switches to days past 48h', () => {
		expect(shortAge(iso('2026-05-01 18:00:00'), now)).toBe('3d');
	});

	it('is empty for a missing timestamp', () => {
		expect(shortAge(null, now)).toBe('');
	});

	it('never renders a negative age', () => {
		expect(shortAge(iso('2026-05-04 19:00:00'), now)).toBe('now');
	});
});

describe('duration', () => {
	it('formats hours and minutes', () => {
		expect(duration(3 * 3600_000 + 38 * 60_000)).toBe('3h 38m');
	});

	it('formats minutes alone', () => {
		expect(duration(45 * 60_000)).toBe('45m');
	});

	it('formats days past 24h', () => {
		expect(duration(26 * 3600_000)).toBe('1d 2h');
	});

	it('is empty for non-positive or missing spans', () => {
		expect(duration(0)).toBe('');
		expect(duration(-5)).toBe('');
		expect(duration(null)).toBe('');
	});
});

describe('countdownTo', () => {
	const now = new Date('2026-05-04T18:00:00Z').getTime();

	it('counts down to a future synthesis', () => {
		expect(countdownTo('2026-05-04 21:38:00', now)).toBe('3h 38m');
	});

	it('reads as imminent once due', () => {
		expect(countdownTo('2026-05-04 17:59:00', now)).toBe('imminent');
	});

	it('is empty without a schedule', () => {
		expect(countdownTo(null, now)).toBe('');
	});
});
