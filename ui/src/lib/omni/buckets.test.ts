// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	BUCKET_ORDER,
	activeBuckets,
	bucketCounts,
	bucketOf,
	cardBucket,
	describeBuckets,
	groupByBucket
} from './buckets';
import type { OmniBucket } from '$lib/api/types';

const card = (status: string, bucket?: OmniBucket, card_id = 'c') =>
	({ status, bucket, card_id }) as never;

describe('bucketOf (client fallback)', () => {
	it('maps failed to changes requested', () => {
		expect(bucketOf({ status: 'failed' } as never)).toBe('changes_requested');
	});

	it('maps terminal statuses to resolved', () => {
		for (const s of ['done', 'dismissed', 'archived']) {
			expect(bucketOf({ status: s } as never)).toBe('resolved');
		}
	});

	it('maps actionable statuses to awaiting you', () => {
		for (const s of ['ready', 'pending', 'awaiting_input', 'requires_approval']) {
			expect(bucketOf({ status: s } as never)).toBe('awaiting_you');
		}
	});

	it('falls through to other', () => {
		expect(bucketOf({ status: 'executing' } as never)).toBe('other');
		expect(bucketOf({ status: 'agent_running' } as never)).toBe('other');
	});
});

describe('cardBucket', () => {
	it('prefers the server-assigned bucket', () => {
		// A merged PR whose card is still `ready`: only the server sees the
		// terminal event type, so its answer must win over the status heuristic.
		expect(cardBucket(card('ready', 'resolved'))).toBe('resolved');
	});

	it('falls back to the status heuristic when absent', () => {
		expect(cardBucket(card('ready'))).toBe('awaiting_you');
	});
});

describe('bucketCounts', () => {
	it('always returns every bucket so chip order stays stable', () => {
		expect(bucketCounts([])).toEqual({
			awaiting_you: 0,
			changes_requested: 0,
			resolved: 0,
			other: 0
		});
	});

	it('counts by the assigned bucket', () => {
		const counts = bucketCounts([
			card('ready', 'awaiting_you'),
			card('ready', 'awaiting_you'),
			card('done', 'resolved'),
			card('failed', 'changes_requested')
		]);
		expect(counts).toEqual({
			awaiting_you: 2,
			changes_requested: 1,
			resolved: 1,
			other: 0
		});
	});
});

describe('activeBuckets', () => {
	it('drops empty buckets and keeps display order', () => {
		const counts = { awaiting_you: 0, changes_requested: 2, resolved: 4, other: 0 };
		expect(activeBuckets(counts)).toEqual(['changes_requested', 'resolved']);
	});

	it('orders work-you-owe before finished work', () => {
		const counts = { awaiting_you: 1, changes_requested: 1, resolved: 1, other: 1 };
		expect(activeBuckets(counts)).toEqual(BUCKET_ORDER);
	});
});

describe('groupByBucket', () => {
	it('groups in BUCKET_ORDER regardless of input order', () => {
		const groups = groupByBucket([
			card('done', 'resolved', 'a'),
			card('ready', 'awaiting_you', 'b'),
			card('failed', 'changes_requested', 'c')
		]);
		expect(groups.map((g) => g.bucket)).toEqual([
			'awaiting_you',
			'changes_requested',
			'resolved'
		]);
	});

	it('preserves the caller order inside a group', () => {
		const groups = groupByBucket([
			card('ready', 'awaiting_you', 'first'),
			card('ready', 'awaiting_you', 'second')
		]);
		expect(groups[0].cards.map((c: { card_id: string }) => c.card_id)).toEqual([
			'first',
			'second'
		]);
	});

	it('omits buckets with no cards', () => {
		expect(groupByBucket([card('done', 'resolved')])).toHaveLength(1);
	});

	it('handles an empty list', () => {
		expect(groupByBucket([])).toEqual([]);
	});
});

describe('describeBuckets', () => {
	it('describes only what it was given', () => {
		const text = describeBuckets([
			card('done', 'resolved'),
			card('done', 'resolved'),
			card('executing', 'other')
		]);
		expect(text).toBe('2 merged / resolved, 1 other activity');
	});

	it('is empty for no cards', () => {
		expect(describeBuckets([])).toBe('');
	});
});
