// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { localToday, feedPrevDate, feedNextDate, allDaysSavedDate } from './feedFilters';

describe('localToday', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	// Freeze the clock with a LOCAL-component Date so the assertion holds in any
	// timezone: localToday() reads the same local getters used to construct it.
	function freezeLocal(y: number, mIndex: number, d: number) {
		vi.useFakeTimers();
		vi.setSystemTime(new Date(y, mIndex, d, 10, 30, 0));
	}

	it('formats the local date as YYYY-MM-DD', () => {
		freezeLocal(2026, 6, 12); // July 12, 2026
		expect(localToday()).toBe('2026-07-12');
	});

	it('zero-pads single-digit month and day', () => {
		freezeLocal(2026, 0, 5); // Jan 5, 2026
		expect(localToday()).toBe('2026-01-05');
	});

	it('handles two-digit month and day', () => {
		freezeLocal(2026, 11, 25); // Dec 25, 2026
		expect(localToday()).toBe('2026-12-25');
	});

	it('always matches the YYYY-MM-DD shape', () => {
		freezeLocal(2026, 2, 9);
		expect(localToday()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
	});
});

describe('feed date navigation stores', () => {
	it('default to no prev/next date and an empty saved date', () => {
		expect(get(feedPrevDate)).toBeNull();
		expect(get(feedNextDate)).toBeNull();
		expect(get(allDaysSavedDate)).toBe('');
	});
});
