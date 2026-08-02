// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// View state for the Omni Situation Board and item page.
//
// The one piece of genuinely persistent state here is the *last-seen version*
// per space. It is what makes the changelog rail's caption ("since you last
// looked") literally true, and what lets the triage column mark items NEW — both
// of which are meaningless without a memory of where the user left off.

import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import type { OmniBucket } from '$lib/api/types';

const SEEN_KEY = 'laya-omni-last-seen';

function readSeen(): Record<string, number> {
	if (!browser) return {};
	try {
		const raw = localStorage.getItem(SEEN_KEY);
		const parsed = raw ? JSON.parse(raw) : {};
		return typeof parsed === 'object' && parsed !== null ? parsed : {};
	} catch {
		return {};
	}
}

/** { space_id: version } — the newest version the user has actually looked at. */
const seenStore = writable<Record<string, number>>(readSeen());

export const omniLastSeen = { subscribe: seenStore.subscribe };

export function lastSeenVersion(spaceId: string): number | null {
	return get(seenStore)[spaceId] ?? null;
}

/**
 * Record that the user has seen `version`.
 *
 * Monotonic on purpose: time-travelling back to an old snapshot must not rewind
 * the mark, or returning to the present would re-announce everything in between
 * as new. Only ever moves forward.
 */
export function markVersionSeen(spaceId: string, version: number): void {
	if (!version) return;
	seenStore.update((current) => {
		if ((current[spaceId] ?? 0) >= version) return current;
		const next = { ...current, [spaceId]: version };
		if (browser) {
			try {
				localStorage.setItem(SEEN_KEY, JSON.stringify(next));
			} catch {
				/* private mode / quota — the mark is a nicety, not a requirement */
			}
		}
		return next;
	});
}

// --- Item page (session-only; nothing here is worth persisting) ---

/** Active outcome filter on the item page. null = show every bucket. */
export const omniItemFilter = writable<OmniBucket | null>(null);

/**
 * card_ids of the expanded evidence rows. Rows open independently rather than
 * as an accordion, so "expand all" / "collapse all" are both meaningful; the
 * page still opens exactly one row by default.
 */
export const omniExpandedCards = writable<Set<string>>(new Set());

/** Whether the user asked to see past the initial 8-row window. */
export const omniShowAllEvidence = writable(false);

/** Reset per-item view state — called when the item page loads a new claim. */
export function resetItemView(): void {
	omniItemFilter.set(null);
	omniExpandedCards.set(new Set());
	omniShowAllEvidence.set(false);
}
