// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Outcome buckets for the Omni item page — the structure behind the LLM's prose.
//
// The aggregate sentence ("4 merged, 3 awaiting your review, 2 with changes
// requested") is rendered verbatim and never parsed. The counts under it come
// from the live state of the evidence cards instead, which the engine already
// assigns server-side (`bucket` on each card) so the API and the UI can't
// disagree. `bucketOf` below is the client-side mirror, used only when a card
// arrives without one — the legacy `?cards=` deep-link path.

import type { OmniBucket, OmniEvidenceCard, ActionCard } from '$lib/api/types';

export const BUCKET_ORDER: OmniBucket[] = [
	'awaiting_you',
	'changes_requested',
	'resolved',
	'other'
];

/** Chip / filter labels — what the bucket is called to the user. */
export const BUCKET_LABELS: Record<OmniBucket, string> = {
	awaiting_you: 'awaiting you',
	changes_requested: 'changes requested',
	resolved: 'merged / resolved',
	other: 'other activity'
};

/**
 * Evidence group headers. Work the user owes comes first — date order is
 * deliberately subordinate to that, which is the whole point of grouping.
 */
export const BUCKET_GROUP_LABELS: Record<OmniBucket, string> = {
	awaiting_you: 'AWAITING YOUR REVIEW',
	changes_requested: 'CHANGES REQUESTED',
	resolved: 'MERGED',
	other: 'OTHER'
};

/** Short status pill text on a collapsed evidence row. */
export const BUCKET_STATUS_LABELS: Record<OmniBucket, string> = {
	awaiting_you: 'REVIEW',
	changes_requested: 'CHANGES',
	resolved: 'MERGED',
	other: 'UPDATE'
};

/**
 * CSS custom-property suffix per bucket. Colours live in app.css so the four
 * appearance combinations (dark/light × glass on/off) and the accessible palette
 * are all resolved in one place rather than branched in every component.
 */
export const BUCKET_TOKEN: Record<OmniBucket, string> = {
	awaiting_you: 'warn',
	changes_requested: 'alert',
	resolved: 'ok',
	other: 'neutral'
};

const AWAITING_STATUSES = new Set(['ready', 'pending', 'awaiting_input', 'requires_approval']);
const TERMINAL_STATUSES = new Set(['done', 'dismissed', 'archived']);

/**
 * Client-side fallback bucketing. Mirrors `_bucket_for` in `omni_api.py`, minus
 * the raw-event-type test — the legacy path has no event data, so a merged PR
 * whose card is still `ready` reads as awaiting. Prefer the server's `bucket`.
 */
export function bucketOf(card: Pick<ActionCard, 'status'>): OmniBucket {
	if (card.status === 'failed') return 'changes_requested';
	if (TERMINAL_STATUSES.has(card.status)) return 'resolved';
	if (AWAITING_STATUSES.has(card.status)) return 'awaiting_you';
	return 'other';
}

export function cardBucket(card: Partial<OmniEvidenceCard> & Pick<ActionCard, 'status'>): OmniBucket {
	return card.bucket ?? bucketOf(card);
}

/** Counts per bucket, always including zero entries so chip order is stable. */
export function bucketCounts(
	cards: Array<Partial<OmniEvidenceCard> & Pick<ActionCard, 'status'>>
): Record<OmniBucket, number> {
	const counts: Record<OmniBucket, number> = {
		awaiting_you: 0,
		changes_requested: 0,
		resolved: 0,
		other: 0
	};
	for (const card of cards) counts[cardBucket(card)] += 1;
	return counts;
}

/** Buckets that actually have cards, in display order. */
export function activeBuckets(counts: Record<OmniBucket, number>): OmniBucket[] {
	return BUCKET_ORDER.filter((b) => counts[b] > 0);
}

/**
 * Cards grouped by bucket, groups in BUCKET_ORDER and empty groups dropped.
 * Within a group the caller's order is preserved (the API returns snapshot
 * order), so the list doesn't reshuffle between reloads.
 */
export function groupByBucket<T extends Partial<OmniEvidenceCard> & Pick<ActionCard, 'status'>>(
	cards: T[]
): Array<{ bucket: OmniBucket; cards: T[] }> {
	const groups = new Map<OmniBucket, T[]>();
	for (const card of cards) {
		const bucket = cardBucket(card);
		const existing = groups.get(bucket);
		if (existing) existing.push(card);
		else groups.set(bucket, [card]);
	}
	return BUCKET_ORDER.filter((b) => groups.has(b)).map((bucket) => ({
		bucket,
		cards: groups.get(bucket)!
	}));
}

/**
 * "3 merged, 5 other activity" — the tail of the pagination row. Describes only
 * what is still hidden, so the user knows what pressing "Show all" reveals.
 */
export function describeBuckets(
	cards: Array<Partial<OmniEvidenceCard> & Pick<ActionCard, 'status'>>
): string {
	const counts = bucketCounts(cards);
	return BUCKET_ORDER.filter((b) => counts[b] > 0)
		.map((b) => `${counts[b]} ${BUCKET_LABELS[b]}`)
		.join(', ');
}
