// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	recentCards,
	recentDrawerOpen,
	trackCardVisit,
	trackGroupVisit,
	clearRecentCards
} from './recentCards';

// The store is a module singleton and persists between tests; clear it each time.
// localStorage is absent under the node test env — the store's try/catch guards
// swallow that, so the in-memory value is what we assert on.
beforeEach(() => {
	clearRecentCards();
	vi.useFakeTimers();
	vi.setSystemTime(new Date(2026, 0, 1, 12, 0, 0));
});
afterEach(() => {
	vi.useRealTimers();
});

describe('trackCardVisit', () => {
	it('adds a visited card to the front of the list', () => {
		trackCardVisit({ card_id: 'c1', header: 'H1', summary: 'S1' });
		const entries = get(recentCards);
		expect(entries).toHaveLength(1);
		expect(entries[0].card_id).toBe('c1');
	});

	it('puts the most recently visited card first', () => {
		trackCardVisit({ card_id: 'c1', header: 'H1', summary: 'S1' });
		vi.advanceTimersByTime(61_000);
		trackCardVisit({ card_id: 'c2', header: 'H2', summary: 'S2' });
		expect(get(recentCards).map((e) => e.card_id)).toEqual(['c2', 'c1']);
	});

	it('updates in place (no duplicate) when re-visited within 60s', () => {
		trackCardVisit({ card_id: 'c1', header: 'old', summary: 'S1' });
		vi.advanceTimersByTime(30_000);
		trackCardVisit({ card_id: 'c1', header: 'new', summary: 'S1b' });
		const entries = get(recentCards);
		expect(entries).toHaveLength(1);
		expect(entries[0].header).toBe('new');
		expect(entries[0].summary).toBe('S1b');
	});

	it('dedups by card_id and moves the re-visit to the top even past the 60s window', () => {
		trackCardVisit({ card_id: 'c1', header: 'H1', summary: 'S1' });
		vi.advanceTimersByTime(61_000);
		trackCardVisit({ card_id: 'c2', header: 'H2', summary: 'S2' });
		vi.advanceTimersByTime(61_000);
		trackCardVisit({ card_id: 'c1', header: 'H1', summary: 'S1' });
		const entries = get(recentCards);
		expect(entries).toHaveLength(2);
		expect(entries.map((e) => e.card_id)).toEqual(['c1', 'c2']);
	});

	it('caps the history at 30 entries, dropping the oldest', () => {
		for (let i = 0; i < 31; i++) {
			trackCardVisit({ card_id: `c${i}`, header: `H${i}`, summary: `S${i}` });
			vi.advanceTimersByTime(61_000); // distinct ids never dedup, but keep timestamps clean
		}
		const entries = get(recentCards);
		expect(entries).toHaveLength(30);
		expect(entries[0].card_id).toBe('c30'); // newest first
		expect(entries.some((e) => e.card_id === 'c0')).toBe(false); // oldest evicted
	});
});

describe('trackGroupVisit', () => {
	it('records a group keyed by entity_id with the headline as the summary', () => {
		trackGroupVisit({
			entity_id: 'jira:proj-1',
			entity_title: 'Project One',
			platform: 'jira',
			card_count: 3,
			group_summary: { headline: 'Sprint at risk', summary: 'details' }
		});
		const entry = get(recentCards)[0];
		expect(entry.card_id).toBe('jira:proj-1');
		expect(entry.entity_id).toBe('jira:proj-1');
		expect(entry.header).toBe('Project One');
		expect(entry.summary).toBe('Sprint at risk');
		expect(entry.source_ref).toBe('jira');
		expect(entry.type).toBe('group');
		expect(entry.card_count).toBe(3);
	});

	it('falls back to an empty summary when there is no group summary', () => {
		trackGroupVisit({ entity_id: 'gh:pr-1', entity_title: 'PR', platform: 'github', card_count: 1, group_summary: null });
		expect(get(recentCards)[0].summary).toBe('');
	});
});

describe('clearRecentCards', () => {
	it('empties the history', () => {
		trackCardVisit({ card_id: 'c1', header: 'H1', summary: 'S1' });
		clearRecentCards();
		expect(get(recentCards)).toEqual([]);
	});
});

describe('recentDrawerOpen', () => {
	it('defaults to closed', () => {
		expect(get(recentDrawerOpen)).toBe(false);
	});
});
