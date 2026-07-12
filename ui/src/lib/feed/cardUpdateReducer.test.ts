// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { ActionCard, CardGroup } from '$lib/api/types';
import {
	isExcludedByFilters,
	isExcludedBySpace,
	computeTopPriority,
	computeHasPending,
	updateAffectsSort,
	removeCardFromGroups,
	applyPayloadToCard,
	reduceCardUpdated,
	type FeedFilterState,
} from './cardUpdateReducer';

// Minimal fixtures — the reducer only reads a handful of fields, so cast partial
// literals rather than construct full API objects.
function card(id: string, over: Partial<ActionCard> = {}): ActionCard {
	return {
		card_id: id,
		status: 'pending',
		priority: 'MEDIUM',
		persona: 'ENGINEER',
		header: `H-${id}`,
		summary: `S-${id}`,
		...over,
	} as ActionCard;
}

function group(entityId: string, cards: ActionCard[], over: Partial<CardGroup> = {}): CardGroup {
	return {
		entity_id: entityId,
		cards,
		top_priority: computeTopPriority(cards),
		has_pending: computeHasPending(cards),
		...over,
	} as CardGroup;
}

const NO_FILTERS: FeedFilterState = { statusFilters: [], showArchived: true, sortBy: 'newest', spaceFilter: [] };

describe('isExcludedByFilters', () => {
	it('hides archived cards when the archive toggle is off', () => {
		expect(isExcludedByFilters('archived', { statusFilters: [], showArchived: false, sortBy: 'newest', spaceFilter: [] })).toBe(true);
	});
	it('keeps archived cards when the archive toggle is on', () => {
		expect(isExcludedByFilters('archived', { statusFilters: [], showArchived: true, sortBy: 'newest', spaceFilter: [] })).toBe(false);
	});
	it('hides a status not in the active status filter', () => {
		expect(isExcludedByFilters('done', { statusFilters: ['pending'], showArchived: true, sortBy: 'newest', spaceFilter: [] })).toBe(true);
	});
	it('keeps a status that is in the active filter', () => {
		expect(isExcludedByFilters('pending', { statusFilters: ['pending'], showArchived: true, sortBy: 'newest', spaceFilter: [] })).toBe(false);
	});
	it('excludes nothing when no status filters are set', () => {
		expect(isExcludedByFilters('done', NO_FILTERS)).toBe(false);
	});
});

describe('isExcludedBySpace', () => {
	it('excludes nothing when the space filter is empty (all spaces)', () => {
		expect(isExcludedBySpace('work', NO_FILTERS)).toBe(false);
	});
	it('hides a card whose space is not in the active space filter', () => {
		expect(isExcludedBySpace('personal', { ...NO_FILTERS, spaceFilter: ['work'] })).toBe(true);
	});
	it('keeps a card whose space is in the active space filter', () => {
		expect(isExcludedBySpace('work', { ...NO_FILTERS, spaceFilter: ['work'] })).toBe(false);
	});
	it('normalizes an empty space id to default', () => {
		expect(isExcludedBySpace('', { ...NO_FILTERS, spaceFilter: ['default'] })).toBe(false);
		expect(isExcludedBySpace('', { ...NO_FILTERS, spaceFilter: ['work'] })).toBe(true);
	});
});

describe('computeTopPriority', () => {
	it('returns the most severe priority present', () => {
		expect(computeTopPriority([card('a', { priority: 'LOW' }), card('b', { priority: 'CRITICAL' }), card('c', { priority: 'MEDIUM' })])).toBe('CRITICAL');
	});
	it('defaults to MEDIUM for an empty group', () => {
		expect(computeTopPriority([])).toBe('MEDIUM');
	});
});

describe('computeHasPending', () => {
	it('is true when any card is pending or ready', () => {
		expect(computeHasPending([card('a', { status: 'done' }), card('b', { status: 'ready' })])).toBe(true);
	});
	it('is false when all cards are terminal', () => {
		expect(computeHasPending([card('a', { status: 'done' }), card('b', { status: 'archived' })])).toBe(false);
	});
});

describe('updateAffectsSort', () => {
	it('priority sort reacts only to a priority change', () => {
		expect(updateAffectsSort('priority', { priority: 'HIGH' })).toBe(true);
		expect(updateAffectsSort('priority', { status: 'done' })).toBe(false);
	});
	it('persona sort reacts only to a persona change', () => {
		expect(updateAffectsSort('persona', { persona: 'OPS' })).toBe(true);
		expect(updateAffectsSort('persona', { priority: 'HIGH' })).toBe(false);
	});
	it('status sort reacts only to a status change', () => {
		expect(updateAffectsSort('status', { status: 'done' })).toBe(true);
	});
	it('newest sort never needs a re-sort', () => {
		expect(updateAffectsSort('newest', { priority: 'CRITICAL', status: 'done' })).toBe(false);
	});
});

describe('removeCardFromGroups', () => {
	it('removes the card and drops groups left empty', () => {
		const groups = [group('e1', [card('a')]), group('e2', [card('b'), card('c')])];
		const out = removeCardFromGroups(groups, 'a');
		expect(out).toHaveLength(1);
		expect(out[0].entity_id).toBe('e2');
		expect(out[0].cards.map((c) => c.card_id)).toEqual(['b', 'c']);
	});
	it('does not mutate the input groups', () => {
		const groups = [group('e2', [card('b'), card('c')])];
		removeCardFromGroups(groups, 'b');
		expect(groups[0].cards).toHaveLength(2);
	});
});

describe('applyPayloadToCard', () => {
	it('applies header/summary/selected_action_id ONLY alongside a status change', () => {
		const base = card('a', { status: 'agent_running' });
		const withStatus = applyPayloadToCard(base, { status: 'done', header: 'New', summary: 'NewS', selected_action_id: 'act1' });
		expect(withStatus.card.header).toBe('New');
		expect(withStatus.card.summary).toBe('NewS');
		expect(withStatus.card.selected_action_id).toBe('act1');
		expect(withStatus.wasAgent).toBe(true);

		// Same header field, but NO status in the payload → header must be ignored.
		const noStatus = applyPayloadToCard(base, { header: 'Ignored' } as never);
		expect(noStatus.card.header).toBe('H-a');
		expect(noStatus.wasAgent).toBe(false);
	});
	it('applies persona/has_workspace unconditionally and can clear bookmarked_at', () => {
		const base = card('a', { bookmarked_at: '2026-07-01' } as Partial<ActionCard>);
		const out = applyPayloadToCard(base, { persona: 'HR', has_workspace: true, bookmarked_at: null });
		expect(out.card.persona).toBe('HR');
		expect(out.card.has_workspace).toBe(true);
		expect(out.card.bookmarked_at).toBeUndefined();
	});
	it('does not mutate the input card', () => {
		const base = card('a', { status: 'pending' });
		applyPayloadToCard(base, { status: 'done' });
		expect(base.status).toBe('pending');
	});
});

describe('reduceCardUpdated', () => {
	it('emits exit/deselect/deselect-from-selection effects when a status is filtered out', () => {
		const groups = [group('e1', [card('a', { status: 'pending' })])];
		const selected = card('a');
		const res = reduceCardUpdated(groups, selected, 'a', { status: 'archived' }, {
			statusFilters: [],
			showArchived: false,
			sortBy: 'newest',
			spaceFilter: [],
		});
		// Groups untouched (the fade-out removes the card later), selection cleared.
		expect(res.groups).toBe(groups);
		expect(res.selectedCard).toBeNull();
		expect(res.effects).toContainEqual({ kind: 'exitThenRemove', cardId: 'a' });
		expect(res.effects).toContainEqual({ kind: 'removeFromSelection', cardId: 'a' });
		expect(res.effects).toContainEqual({ kind: 'deselect' });
	});

	it('does not deselect a filtered-out card that was not the open one', () => {
		const groups = [group('e1', [card('a', { status: 'pending' })])];
		const res = reduceCardUpdated(groups, card('other'), 'a', { status: 'archived' }, {
			statusFilters: [],
			showArchived: false,
			sortBy: 'newest',
			spaceFilter: [],
		});
		expect(res.selectedCard?.card_id).toBe('other');
		expect(res.effects.some((e) => e.kind === 'deselect')).toBe(false);
	});

	it('updates a card in place and recomputes the group top_priority', () => {
		const groups = [group('e1', [card('a', { priority: 'CRITICAL' }), card('b', { priority: 'LOW' })])];
		const res = reduceCardUpdated(groups, null, 'a', { priority: 'LOW' }, NO_FILTERS);
		const g = res.groups[0];
		expect(g.cards[0].priority).toBe('LOW');
		expect(g.top_priority).toBe('LOW'); // was CRITICAL, both now LOW
		expect(res.effects).toEqual([]); // newest sort → no reload
	});

	it('reloads when the change affects the active sort', () => {
		const groups = [group('e1', [card('a', { priority: 'CRITICAL' })])];
		const res = reduceCardUpdated(groups, null, 'a', { priority: 'LOW' }, { ...NO_FILTERS, sortBy: 'priority' });
		expect(res.effects).toContainEqual({ kind: 'reload' });
	});

	it('reloads when an agent card reaches a terminal status', () => {
		const groups = [group('e1', [card('a', { status: 'agent_running' })])];
		const res = reduceCardUpdated(groups, null, 'a', { status: 'done' }, NO_FILTERS);
		expect(res.groups[0].cards[0].status).toBe('done');
		expect(res.groups[0].has_pending).toBe(false);
		expect(res.effects).toContainEqual({ kind: 'reload' });
	});

	it('syncs the open detail card when it is the one updated', () => {
		const groups = [group('e1', [card('a', { status: 'pending' })])];
		const res = reduceCardUpdated(groups, card('a', { status: 'pending' }), 'a', { status: 'done', header: 'Fresh' }, NO_FILTERS);
		expect(res.selectedCard?.status).toBe('done');
		expect(res.selectedCard?.header).toBe('Fresh');
	});

	it('reloads and leaves state untouched when the card is not present', () => {
		const groups = [group('e1', [card('a')])];
		const res = reduceCardUpdated(groups, null, 'missing', { priority: 'HIGH' }, NO_FILTERS);
		expect(res.groups).toBe(groups);
		expect(res.effects).toEqual([{ kind: 'reload' }]);
	});

	it('does not mutate the input groups on an in-place update', () => {
		const groups = [group('e1', [card('a', { priority: 'CRITICAL' })])];
		reduceCardUpdated(groups, null, 'a', { priority: 'LOW' }, NO_FILTERS);
		expect(groups[0].cards[0].priority).toBe('CRITICAL');
	});

	it('exits a card moved out of the currently-viewed space', () => {
		const groups = [group('e1', [card('a', { space_id: 'work' } as Partial<ActionCard>)])];
		const res = reduceCardUpdated(groups, card('a'), 'a', { space_id: 'personal' }, { ...NO_FILTERS, spaceFilter: ['work'] });
		expect(res.groups).toBe(groups); // fade-out owns removal
		expect(res.selectedCard).toBeNull();
		expect(res.effects).toContainEqual({ kind: 'exitThenRemove', cardId: 'a' });
		expect(res.effects).toContainEqual({ kind: 'deselect' });
	});

	it('keeps a moved card in place and updates its space when viewing all spaces', () => {
		const groups = [group('e1', [card('a', { space_id: 'work' } as Partial<ActionCard>)])];
		const res = reduceCardUpdated(groups, null, 'a', { space_id: 'personal' }, NO_FILTERS);
		expect(res.groups[0].cards[0].space_id).toBe('personal');
		expect(res.effects).toEqual([]); // no re-section on a space change in all-spaces view
	});
});
