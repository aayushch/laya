// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import type { OmniChangeSummary } from '$lib/api/types';

/**
 * Net change in the number of open attention items between the comparison base
 * and the displayed version: attention-count-now minus attention-count-at-base,
 * read from the engine's merged change summary rather than diffed client-side
 * (the base version's items are never in the browser).
 *
 * Each merged entry is an item's last recorded state in the range, with
 * `entered_in_range` telling whether the item existed at the base. So:
 * - an `added` entry in attention counts +1 only if it entered in range
 *   (an item dropped and re-added was already there: no change);
 * - a `resolved` or `folded`-from-attention entry counts −1 only if it did NOT
 *   enter in range (an item that arrived and left nets to zero).
 * Entries without the flag (summaries not produced by a range merge) are read
 * as plain arrivals and departures.
 *
 * Returns null when nothing changed, so the instrument can omit the figure.
 */
export function attentionDelta(changes: OmniChangeSummary | null | undefined): number | null {
	if (!changes) return null;
	const arrivals = changes.added.filter(
		(a) => a.section === 'attention' && (a.entered_in_range ?? true)
	).length;
	const departures =
		changes.resolved.filter((r) => r.section === 'attention' && !(r.entered_in_range ?? false))
			.length +
		changes.folded.filter((f) => f.from_section === 'attention' && !(f.entered_in_range ?? false))
			.length;
	const delta = arrivals - departures;
	return delta === 0 ? null : delta;
}
