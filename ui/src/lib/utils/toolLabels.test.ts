// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { humanizeToolName, toolLabels } from './toolLabels';

describe('humanizeToolName', () => {
	it('turns snake_case tool names into sentence-case labels', () => {
		expect(humanizeToolName('search_cards')).toBe('Search cards');
		expect(humanizeToolName('get_event')).toBe('Get event');
		expect(humanizeToolName('get_card_stats')).toBe('Get card stats');
	});
	it('leaves a single word capitalised and an empty name alone', () => {
		expect(humanizeToolName('search')).toBe('Search');
		expect(humanizeToolName('')).toBe('');
	});
});

describe('toolLabels', () => {
	it('lists each tool once, in first-call order', () => {
		expect(toolLabels(['search_cards', 'get_event', 'search_cards'])).toEqual(['Search cards', 'Get event']);
	});
	it('is empty for messages without tools', () => {
		expect(toolLabels(undefined)).toEqual([]);
		expect(toolLabels([])).toEqual([]);
	});
});
