// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, afterEach } from 'vitest';
import { parseBackendDate, timeAgo } from './datetime';

describe('parseBackendDate', () => {
	it('returns null for empty/nullish input', () => {
		expect(parseBackendDate(null)).toBeNull();
		expect(parseBackendDate(undefined)).toBeNull();
		expect(parseBackendDate('')).toBeNull();
	});

	it('interprets a zone-less, space-separated backend timestamp as UTC', () => {
		// The whole point of this helper: "2026-07-01 06:40:04" must NOT be read as
		// local time. Anchored to UTC, its absolute instant is fixed regardless of
		// the machine's timezone.
		const d = parseBackendDate('2026-07-01 06:40:04');
		expect(d?.toISOString()).toBe('2026-07-01T06:40:04.000Z');
	});

	it('converts the space separator to T (WebKit/WKWebView rejects the space form)', () => {
		// A zone-less input is normalised to "YYYY-MM-DDTHH:MM:SSZ"; parsing the
		// result proves the T-swap happened and the instant is intact.
		const d = parseBackendDate('2026-01-15 23:59:59');
		expect(d?.toISOString()).toBe('2026-01-15T23:59:59.000Z');
	});

	it('passes through a value that already carries a trailing Z', () => {
		const d = parseBackendDate('2026-07-01T06:40:04Z');
		expect(d?.toISOString()).toBe('2026-07-01T06:40:04.000Z');
	});

	it('passes through a value that already carries a numeric offset', () => {
		// "+05:30" is +5h30m ahead of UTC, so the UTC instant is 5.5h earlier.
		const d = parseBackendDate('2026-07-01T12:00:00+05:30');
		expect(d?.toISOString()).toBe('2026-07-01T06:30:00.000Z');
	});
});

describe('timeAgo', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	// Anchor "now" to a fixed absolute instant; the backend strings below are all
	// UTC-anchored, so every diff is timezone-independent.
	function freezeAt(iso: string) {
		vi.useFakeTimers();
		vi.setSystemTime(new Date(iso));
	}

	it('returns the null label for empty input (default empty string)', () => {
		expect(timeAgo(null)).toBe('');
		expect(timeAgo(undefined)).toBe('');
		expect(timeAgo('', { nullLabel: 'never' })).toBe('never');
	});

	it('shows "just now" under a minute', () => {
		freezeAt('2026-07-01T12:00:30Z');
		expect(timeAgo('2026-07-01 12:00:00')).toBe('just now');
	});

	it('shows minutes under an hour', () => {
		freezeAt('2026-07-01T12:45:00Z');
		expect(timeAgo('2026-07-01 12:00:00')).toBe('45m ago');
	});

	it('shows hours under a day', () => {
		freezeAt('2026-07-01T20:00:00Z');
		expect(timeAgo('2026-07-01 12:00:00')).toBe('8h ago');
	});

	it('shows days past 24h', () => {
		freezeAt('2026-07-04T12:00:00Z');
		expect(timeAgo('2026-07-01 12:00:00')).toBe('3d ago');
	});

	it('collapses 7+ days into weeks only when weeks:true', () => {
		freezeAt('2026-07-20T12:00:00Z'); // 19 days later
		expect(timeAgo('2026-07-01 12:00:00')).toBe('19d ago');
		expect(timeAgo('2026-07-01 12:00:00', { weeks: true })).toBe('2w ago');
	});

	it('does not switch to weeks below 7 days even with weeks:true', () => {
		freezeAt('2026-07-07T12:00:00Z'); // 6 days
		expect(timeAgo('2026-07-01 12:00:00', { weeks: true })).toBe('6d ago');
	});
});
