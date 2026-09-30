// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import type { ProviderModels } from '$lib/api/types';

/** "retires Oct 23, 2026", or null without a date. Formatted in UTC so the day doesn't shift. */
export function retirementLabel(retiresOn: string | undefined): string | null {
	if (!retiresOn) return null;
	const date = new Date(`${retiresOn}T00:00:00Z`);
	if (Number.isNaN(date.getTime())) return null;
	const formatted = date.toLocaleDateString('en-US', {
		month: 'short',
		day: 'numeric',
		year: 'numeric',
		timeZone: 'UTC'
	});
	return `retires ${formatted}`;
}

/** "Couldn't check with <provider> — list may be out of date" when unverified, else null. */
export function unverifiedNote(group: ProviderModels): string | null {
	return group.verified === false
		? `Couldn't check with ${group.label} — list may be out of date`
		: null;
}
