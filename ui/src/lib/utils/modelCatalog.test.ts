// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { ProviderModels } from '$lib/api/types';
import { retirementLabel, unverifiedNote } from './modelCatalog';

function group(overrides: Partial<ProviderModels> = {}): ProviderModels {
	return { provider: 'google', label: 'Google', models: [], ...overrides };
}

describe('retirementLabel', () => {
	it('formats YYYY-MM-DD without shifting the day across timezones', () => {
		expect(retirementLabel('2026-10-23')).toBe('retires Oct 23, 2026');
		expect(retirementLabel('2027-01-01')).toBe('retires Jan 1, 2027');
	});

	it('returns null without a date', () => {
		expect(retirementLabel(undefined)).toBeNull();
		expect(retirementLabel('')).toBeNull();
		expect(retirementLabel('not-a-date')).toBeNull();
	});
});

describe('unverifiedNote', () => {
	it('names the provider when verified is false', () => {
		expect(unverifiedNote(group({ verified: false }))).toBe(
			"Couldn't check with Google — list may be out of date"
		);
	});

	it('is null for verified lists and for providers that omit the flag', () => {
		expect(unverifiedNote(group({ verified: true }))).toBeNull();
		expect(unverifiedNote(group())).toBeNull();
	});
});
