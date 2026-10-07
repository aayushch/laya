// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { OmniChangeFolded, OmniChangeSummary, OmniSectionType } from '$lib/api/types';
import { attentionDelta } from './changes';

function summary(partial: Partial<OmniChangeSummary>): OmniChangeSummary {
	const s: OmniChangeSummary = {
		added: [],
		folded: [],
		resolved: [],
		counts: { added: 0, folded: 0, resolved: 0 },
		...partial
	};
	s.counts = { added: s.added.length, folded: s.folded.length, resolved: s.resolved.length };
	return s;
}

let seq = 0;
const added = (section: OmniSectionType, extra: { promoted_from?: 'recent'; entered_in_range?: boolean } = {}) => ({
	item_key: `k${seq++}`,
	section,
	text: 't',
	source_count: 1,
	platforms: [],
	...extra
});
const folded = (
	from_section: OmniSectionType,
	to_section: OmniSectionType | null,
	entered_in_range?: boolean
): OmniChangeFolded => ({
	item_key: `k${seq++}`,
	from_section,
	to_section,
	from_text: 't',
	to_text: to_section ? 'u' : null,
	...(entered_in_range === undefined ? {} : { entered_in_range })
});

describe('attentionDelta', () => {
	it('is null with no summary or no attention movement', () => {
		expect(attentionDelta(null)).toBeNull();
		expect(attentionDelta(summary({}))).toBeNull();
		expect(attentionDelta(summary({ added: [added('recent')] }))).toBeNull();
		expect(attentionDelta(summary({ folded: [folded('recent', 'period')] }))).toBeNull();
	});

	it('counts arrivals, including promotions from recent', () => {
		expect(
			attentionDelta(summary({ added: [added('attention'), added('attention', { promoted_from: 'recent' })] }))
		).toBe(2);
	});

	it('counts resolved and folded-from-attention as departures', () => {
		const s = summary({
			resolved: [{ item_key: 'r', section: 'attention', text: 't', entity_ids: [], resolved_at: null }],
			folded: [folded('attention', null), folded('attention', 'recent')]
		});
		expect(attentionDelta(s)).toBe(-3);
	});

	it('ignores departures of items that entered within the range', () => {
		expect(attentionDelta(summary({ folded: [folded('attention', null, true)] }))).toBeNull();
	});

	it('ignores a re-add of an item the base already had', () => {
		expect(
			attentionDelta(summary({ added: [added('attention', { entered_in_range: false })] }))
		).toBeNull();
	});

	it('nets to count-now minus count-at-base', () => {
		// Base had two attention items; one left, one new one arrived, one
		// transient item came and went: 2 − 1 + 1 = 2 → delta 0.
		const s = summary({
			added: [added('attention', { entered_in_range: true })],
			folded: [folded('attention', null, false), folded('attention', null, true)]
		});
		expect(attentionDelta(s)).toBeNull();
	});
});
