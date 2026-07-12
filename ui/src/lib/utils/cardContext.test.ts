// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { ActionCard } from '$lib/api/types';
import { buildCardContext, buildSingleCardContext } from './cardContext';

// The builder reads a fixed set of fields; cast partial literals like the feed
// reducer test does rather than construct full API objects.
function card(over: Partial<ActionCard> = {}): ActionCard {
	return {
		card_id: 'c1',
		header: 'Deploy blocked',
		summary: 'CI is red on main',
		priority: 'HIGH',
		status: 'pending',
		persona: 'ENGINEER',
		category: 'incident',
		entity_id: 'github:pr-42',
		...over
	} as ActionCard;
}

describe('buildCardContext', () => {
	it('reports the card count in the header', () => {
		expect(buildCardContext([])).toContain('viewing 0 related card(s).');
		expect(buildCardContext([card(), card()])).toContain('viewing 2 related card(s).');
	});

	it('renders the core fields for a card', () => {
		const out = buildCardContext([card()]);
		expect(out).toContain('--- Card: c1 ---');
		expect(out).toContain('Title: Deploy blocked');
		expect(out).toContain('Summary: CI is red on main');
		expect(out).toContain('Priority: HIGH | Status: pending | Persona: ENGINEER | Category: incident');
	});

	it('derives the platform from the entity id prefix', () => {
		expect(buildCardContext([card({ entity_id: 'slack:msg-9' })])).toContain('Platform: slack');
	});

	it('falls back to "unknown" platform when there is no entity id', () => {
		expect(buildCardContext([card({ entity_id: undefined })])).toContain('Platform: unknown');
	});

	it('omits optional lines that are absent', () => {
		const out = buildCardContext([card()]);
		expect(out).not.toContain('Actor:');
		expect(out).not.toContain('Intelligence:');
		expect(out).not.toContain('Staged Output');
		expect(out).not.toContain('Source:');
		expect(out).not.toContain('URL:');
	});

	it('includes optional lines when present', () => {
		const out = buildCardContext([
			card({
				actor_name: 'Jane Doe',
				intelligence: ['blocks release', 'flaky test'],
				staged_output: { type: 'draft_reply', content: 'On it.' },
				source_ref: 'PR #42',
				source_url: 'https://example.com/pr/42'
			} as Partial<ActionCard>)
		]);
		expect(out).toContain('Actor: Jane Doe');
		expect(out).toContain('Intelligence:');
		expect(out).toContain('- blocks release');
		expect(out).toContain('- flaky test');
		expect(out).toContain('Staged Output (draft_reply):');
		expect(out).toContain('On it.');
		expect(out).toContain('Source: PR #42');
		expect(out).toContain('URL: https://example.com/pr/42');
	});

	it('omits the Intelligence block for an empty intelligence array', () => {
		const out = buildCardContext([card({ intelligence: [] } as Partial<ActionCard>)]);
		expect(out).not.toContain('Intelligence:');
	});
});

describe('buildSingleCardContext', () => {
	it('is equivalent to building a one-card list', () => {
		const c = card();
		expect(buildSingleCardContext(c)).toBe(buildCardContext([c]));
		expect(buildSingleCardContext(c)).toContain('viewing 1 related card(s).');
	});
});
