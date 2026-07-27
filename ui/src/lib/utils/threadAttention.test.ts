// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { ActionCard } from '$lib/api/types';
import { threadAttention } from './threadAttention';

const NOW = new Date('2026-05-02T16:00:00Z');

/** Backend format: UTC, space-separated, no zone designator. */
function ts(hoursAgo: number): string {
	return new Date(NOW.getTime() - hoursAgo * 3600_000).toISOString().replace('T', ' ').slice(0, 19);
}

function card(overrides: Partial<ActionCard> = {}): ActionCard {
	return {
		card_id: 'card_1',
		event_id: 'evt_1',
		created_at: ts(1),
		priority: 'MEDIUM',
		persona: 'ENGINEER',
		category: 'CODE',
		header: 'Header',
		summary: 'Summary',
		status: 'ready',
		privacy_tier: 1,
		has_workspace: false,
		...overrides
	} as ActionCard;
}

describe('threadAttention', () => {
	it('returns a quiet state for an empty thread', () => {
		expect(threadAttention([], { now: NOW })).toMatchObject({
			escalating: false,
			agentRunning: false,
			awaitingInput: false,
			needsYou: false
		});
	});

	it('escalates when the latest card failed', () => {
		const result = threadAttention([card({ status: 'failed' })], { now: NOW });
		expect(result.escalating).toBe(true);
		expect(result.reason).toMatch(/failed/i);
	});

	it('does not escalate a single fresh card', () => {
		expect(threadAttention([card()], { now: NOW }).escalating).toBe(false);
	});

	it('escalates a HIGH follow-up chain left unanswered past the stale window', () => {
		const result = threadAttention(
			[
				card({ card_id: 'a', created_at: ts(9), priority: 'HIGH' }),
				card({ card_id: 'b', created_at: ts(7), priority: 'HIGH' }),
				card({ card_id: 'c', created_at: ts(6), priority: 'HIGH' })
			],
			{ now: NOW }
		);
		expect(result.escalating).toBe(true);
		expect(result.reason).toMatch(/Unanswered/);
	});

	it('does not escalate an aged MEDIUM thread — ageing alone is not burning', () => {
		// Without this gate every unactioned thread on a day you are not working
		// turns red (62 of 121 threads on a real day).
		const result = threadAttention(
			[
				card({ card_id: 'a', created_at: ts(9) }),
				card({ card_id: 'b', created_at: ts(7) }),
				card({ card_id: 'c', created_at: ts(6) })
			],
			{ now: NOW }
		);
		expect(result.escalating).toBe(false);
	});

	it('does not escalate a HIGH thread with only two events', () => {
		const result = threadAttention(
			[
				card({ card_id: 'a', created_at: ts(9), priority: 'HIGH' }),
				card({ card_id: 'b', created_at: ts(6), priority: 'HIGH' })
			],
			{ now: NOW }
		);
		expect(result.escalating).toBe(false);
	});

	it('does not escalate an aged thread that is fully resolved', () => {
		const result = threadAttention(
			[
				card({ card_id: 'a', created_at: ts(9), priority: 'HIGH', status: 'done' }),
				card({ card_id: 'b', created_at: ts(7), priority: 'HIGH', status: 'done' }),
				card({ card_id: 'c', created_at: ts(6), priority: 'HIGH', status: 'done' })
			],
			{ now: NOW }
		);
		expect(result.escalating).toBe(false);
	});

	it('escalates a CRITICAL thread that has stayed open too long', () => {
		const result = threadAttention([card({ priority: 'CRITICAL', created_at: ts(7) })], { now: NOW });
		expect(result.escalating).toBe(true);
		expect(result.reason).toMatch(/Critical/);
	});

	it('respects tuned thresholds', () => {
		const cards = [
			card({ card_id: 'a', created_at: ts(9), priority: 'HIGH' }),
			card({ card_id: 'b', created_at: ts(7), priority: 'HIGH' }),
			card({ card_id: 'c', created_at: ts(6), priority: 'HIGH' })
		];
		expect(threadAttention(cards, { now: NOW }).escalating).toBe(true);
		expect(threadAttention(cards, { now: NOW, staleHours: 12 }).escalating).toBe(false);
		expect(threadAttention(cards, { now: NOW, minStaleEvents: 4 }).escalating).toBe(false);
	});

	it('flags a running agent', () => {
		const result = threadAttention([card({ status: 'agent_running' })], { now: NOW });
		expect(result.agentRunning).toBe(true);
		expect(result.escalating).toBe(false);
	});

	it('flags awaiting input, including an open workspace card', () => {
		expect(threadAttention([card({ status: 'awaiting_input' })], { now: NOW }).awaitingInput).toBe(true);
		expect(threadAttention([card({ status: 'ready', has_workspace: true })], { now: NOW }).awaitingInput).toBe(true);
	});

	it('does not treat a finished workspace card as awaiting input', () => {
		expect(threadAttention([card({ status: 'done', has_workspace: true })], { now: NOW }).awaitingInput).toBe(false);
	});

	it('marks ready cards as needing you, and terminal ones as not', () => {
		expect(threadAttention([card({ status: 'ready' })], { now: NOW }).needsYou).toBe(true);
		expect(threadAttention([card({ status: 'dismissed' })], { now: NOW }).needsYou).toBe(false);
		expect(threadAttention([card({ status: 'archived' })], { now: NOW }).needsYou).toBe(false);
	});

	it('reads the latest card by time, not array order', () => {
		const result = threadAttention(
			[card({ card_id: 'new', created_at: ts(1), status: 'done' }), card({ card_id: 'old', created_at: ts(5), status: 'failed' })],
			{ now: NOW }
		);
		expect(result.escalating).toBe(false); // latest is the resolved one
	});
});
