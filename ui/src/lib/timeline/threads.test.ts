// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { ActionCard, CardGroup } from '$lib/api/types';
import {
	buildThreads,
	layoutMeetings,
	statusTone,
	attentionMarks,
	shortEntityLabel,
	entityLabels
} from './threads';

const DATE = '2026-05-02';
const NOW = new Date(2026, 4, 2, 16, 0, 0);

/** Backend timestamps are UTC without a designator; build one from local time. */
function backendTs(d: Date): string {
	return d.toISOString().replace('T', ' ').slice(0, 19);
}

function localTs(hour: number, minute = 0, day = 2): string {
	return backendTs(new Date(2026, 4, day, hour, minute, 0));
}

function card(overrides: Partial<ActionCard> = {}): ActionCard {
	return {
		card_id: 'card_1',
		event_id: 'evt_1',
		created_at: localTs(9, 30),
		priority: 'HIGH',
		persona: 'ENGINEER',
		category: 'CODE',
		header: 'Fix NPE on null customer ID',
		summary: 'Summary',
		status: 'ready',
		privacy_tier: 1,
		has_workspace: false,
		...overrides
	} as ActionCard;
}

function group(cards: ActionCard[], overrides: Partial<CardGroup> = {}): CardGroup {
	return {
		entity_id: 'github:PR-412',
		entity_title: 'PR #412',
		platform: 'github',
		card_count: cards.length,
		top_priority: 'HIGH',
		latest_at: cards[cards.length - 1]?.created_at ?? '',
		has_pending: false,
		unread_count: 0,
		cards,
		...overrides
	} as CardGroup;
}

describe('buildThreads', () => {
	it('spans a thread from its first to its last event of the day', () => {
		const threads = buildThreads(
			[group([card({ card_id: 'a', created_at: localTs(9, 12) }), card({ card_id: 'b', created_at: localTs(15, 5) })])],
			{ date: DATE, now: NOW }
		);
		expect(threads).toHaveLength(1);
		expect(threads[0].firstMinute).toBe(552);
		expect(threads[0].lastMinute).toBe(905);
		expect(threads[0].openHours).toBeCloseTo(5.88, 1);
	});

	it('orders events chronologically regardless of payload order', () => {
		const threads = buildThreads(
			[group([card({ card_id: 'late', created_at: localTs(15, 5) }), card({ card_id: 'early', created_at: localTs(9, 12) })])],
			{ date: DATE, now: NOW }
		);
		expect(threads[0].events.map((e) => e.cardId)).toEqual(['early', 'late']);
		expect(threads[0].latest.cardId).toBe('late');
	});

	it('pins carried-forward cards to the start of the day and flags the thread', () => {
		const threads = buildThreads(
			[group([card({ card_id: 'yesterday', created_at: localTs(14, 0, 1) }), card({ card_id: 'today', created_at: localTs(10, 0) })])],
			{ date: DATE, now: NOW }
		);
		expect(threads[0].carriedForward).toBe(true);
		expect(threads[0].events[0].carried).toBe(true);
		expect(threads[0].events[0].minute).toBe(0);
		// Geometry comes from today's events only.
		expect(threads[0].firstMinute).toBe(600);
	});

	it('skips groups with no cards', () => {
		expect(buildThreads([group([])], { date: DATE, now: NOW })).toEqual([]);
	});

	it('carries the group through so a click can open the existing detail panel', () => {
		const g = group([card()]);
		const [thread] = buildThreads([g], { date: DATE, now: NOW });
		expect(thread.group).toBe(g);
		expect(thread.singleCard).toBe(true);
		expect(thread.entityId).toBe('github:PR-412');
		expect(thread.platform).toBe('github');
	});

	it('marks multi-card groups as not single', () => {
		const [thread] = buildThreads(
			[group([card({ card_id: 'a' }), card({ card_id: 'b', created_at: localTs(11) })], { card_count: 2 })],
			{ date: DATE, now: NOW }
		);
		expect(thread.singleCard).toBe(false);
	});

	it('derives attention state from the cards in the thread', () => {
		const [thread] = buildThreads([group([card({ status: 'agent_running' })])], { date: DATE, now: NOW });
		expect(thread.attention.agentRunning).toBe(true);
	});
});

describe('shortEntityLabel', () => {
	it('takes the last path segment of a repo-scoped id', () => {
		expect(shortEntityLabel('bitbucket:pull_request:groundlabs/ferret-backend/PR-852')).toBe('PR-852');
		expect(shortEntityLabel('github:pull_request:acme/web/PR-12')).toBe('PR-12');
	});
	it('keeps ticket keys whole', () => {
		expect(shortEntityLabel('jira:ticket:FERR-1585')).toBe('FERR-1585');
		expect(shortEntityLabel('linear:issue:ENG-42')).toBe('ENG-42');
	});
	it('reduces a slack thread id to its channel', () => {
		expect(shortEntityLabel('slack:thread:thread-C08JSD56XNE-1785308884.008409')).toBe('C08JSD56XNE');
	});
	it('truncates opaque ids rather than blowing out the lane', () => {
		const label = shortEntityLabel('gmail:email_thread:19fa84e9125ad3d7');
		expect(label.length).toBeLessThanOrEqual(14);
		expect(label.endsWith('…')).toBe(true);
	});
	it('handles a bare id and empty input', () => {
		expect(shortEntityLabel('PR-9')).toBe('PR-9');
		expect(shortEntityLabel(undefined)).toBe('');
		expect(shortEntityLabel('')).toBe('');
	});
});

describe('entityLabels', () => {
	it('puts the identifying key first in a mixed context group', () => {
		// Exactly the reported case: a PR, a gmail thread and a slack thread in one
		// context group — only the PR key is worth the first slot.
		const labels = entityLabels([
			card({ card_id: 'a', entity_id: 'gmail:email_thread:19fa84e9125ad3d7' }),
			card({ card_id: 'b', entity_id: 'slack:thread:thread-C08JSD56XNE-1785308884.008409' }),
			card({ card_id: 'c', entity_id: 'bitbucket:pull_request:groundlabs/ferret-backend/PR-852' })
		]);
		expect(labels[0]).toBe('PR-852');
		expect(labels).toHaveLength(3);
	});
	it('dedupes entities that appear on several cards', () => {
		const labels = entityLabels([
			card({ card_id: 'a', entity_id: 'jira:ticket:FERR-1585' }),
			card({ card_id: 'b', entity_id: 'jira:ticket:FERR-1585' })
		]);
		expect(labels).toEqual(['FERR-1585']);
	});
	it('is stable within a rank so chronology still shows through', () => {
		const labels = entityLabels([
			card({ card_id: 'a', entity_id: 'jira:ticket:FERR-2' }),
			card({ card_id: 'b', entity_id: 'jira:ticket:FERR-1' })
		]);
		expect(labels).toEqual(['FERR-2', 'FERR-1']);
	});
	it('ignores cards with no entity', () => {
		expect(entityLabels([card({ entity_id: undefined })])).toEqual([]);
	});
});

describe('statusTone', () => {
	it('maps statuses onto the five visual families', () => {
		expect(statusTone('failed')).toBe('failed');
		expect(statusTone('agent_running')).toBe('running');
		expect(statusTone('awaiting_input')).toBe('running');
		expect(statusTone('done')).toBe('done');
		expect(statusTone('ready')).toBe('ready');
		expect(statusTone('pending')).toBe('ready');
		expect(statusTone('archived')).toBe('dormant');
	});
});

describe('attentionMarks', () => {
	it('emits one mark per attention-worthy thread, escalation first', () => {
		const threads = buildThreads(
			[
				group([card({ card_id: 'f', status: 'failed' })], { entity_id: 'a' }),
				group([card({ card_id: 'r', status: 'agent_running' })], { entity_id: 'b' }),
				group([card({ card_id: 'q', status: 'done' })], { entity_id: 'c' })
			],
			{ date: DATE, now: NOW }
		);
		const marks = attentionMarks(threads);
		expect(marks.map((m) => m.kind)).toEqual(['escalating', 'agent']);
	});
});

describe('layoutMeetings', () => {
	it('gives a lone meeting the full rail', () => {
		const [block] = layoutMeetings([{ meeting: 'standup', startMin: 570, endMin: 585 }]);
		expect(block).toMatchObject({ slot: 0, slots: 1 });
	});

	it('splits the rail between colliding meetings', () => {
		const blocks = layoutMeetings([
			{ meeting: 'incident', startMin: 960, endMin: 1020 },
			{ meeting: 'board', startMin: 960, endMin: 1050 }
		]);
		expect(blocks.map((b) => b.slot)).toEqual([0, 1]);
		expect(blocks.every((b) => b.slots === 2)).toBe(true);
	});

	it('starts a fresh cluster once the overlap ends', () => {
		const blocks = layoutMeetings([
			{ meeting: 'a', startMin: 540, endMin: 600 },
			{ meeting: 'b', startMin: 570, endMin: 630 },
			{ meeting: 'c', startMin: 700, endMin: 730 }
		]);
		expect(blocks.find((b) => b.meeting === 'c')).toMatchObject({ slot: 0, slots: 1 });
		expect(blocks.find((b) => b.meeting === 'a')?.slots).toBe(2);
	});

	it('handles three-way overlaps', () => {
		const blocks = layoutMeetings([
			{ meeting: 'a', startMin: 540, endMin: 660 },
			{ meeting: 'b', startMin: 550, endMin: 620 },
			{ meeting: 'c', startMin: 560, endMin: 600 }
		]);
		expect(blocks.every((b) => b.slots === 3)).toBe(true);
	});
});
