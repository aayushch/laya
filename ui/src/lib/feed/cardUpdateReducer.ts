// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Pure reducer for the feed's `card_updated` WebSocket message (P7-7).
 *
 * This logic used to live inline in the 2,701-line `feed/+page.svelte` $effect,
 * welded to component state and side effects (scheduleReload, sessionStorage,
 * setTimeout, feedSelection). It is the highest bug-density code in the feed —
 * e.g. a reclassified card silently failing to re-sort because `top_priority` /
 * `sort_key` are server-computed but only mutated in place (the
 * feed_ws_stale_group_fields fix).
 *
 * Extracting it as a pure `(state, message) -> {state, effects}` function lets
 * every transition be unit-tested in isolation. The component stays responsible
 * for *running* the returned effects; the reducer only *decides* them.
 */

import type { ActionCard, CardGroup } from '$lib/api/types';

/** Priority severity rank — mirrors backend `_PRIORITY_ORDER` (cards_api.py).
 *  Lower = more severe. Used to recompute a group's top_priority. */
export const PRIORITY_RANK: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

/** The subset of card fields a `card_updated` message can carry. */
export interface CardUpdatePayload {
	status?: string;
	header?: string;
	summary?: string;
	priority?: string;
	persona?: string;
	category?: string;
	has_workspace?: boolean;
	session_id?: string;
	selected_action_id?: string;
	bookmarked_at?: string | null;
	/** New space the card was moved to (move-to-space feature). */
	space_id?: string;
}

/** The slice of feed filter state the reducer needs. */
export interface FeedFilterState {
	statusFilters: string[];
	showArchived: boolean;
	sortBy: string;
	/** Active space multi-select; empty = "all spaces" (nothing excluded). */
	spaceFilter: string[];
}

/**
 * Side effects the component must run after applying the returned state.
 * The reducer is pure, so it names effects rather than performing them.
 */
export type CardUpdateEffect =
	/** Refetch the grouped feed (group ordering / section bucketing went stale). */
	| { kind: 'reload' }
	/** Drop the card from the multi-select set (feedSelection.removeDeleted). */
	| { kind: 'removeFromSelection'; cardId: string }
	/** Clear the open detail panel + its persisted sessionStorage key. */
	| { kind: 'deselect' }
	/** Fade the card out, then remove it from its group after the exit transition. */
	| { kind: 'exitThenRemove'; cardId: string };

export interface CardUpdateResult {
	groups: CardGroup[];
	selectedCard: ActionCard | null;
	effects: CardUpdateEffect[];
}

/** Would a card with `status` be hidden by the currently-active feed filters? */
export function isExcludedByFilters(status: string, filters: FeedFilterState): boolean {
	const isArchived = status === 'archived';
	const excludedByArchiveToggle = isArchived && !filters.showArchived;
	const excludedByStatusFilter =
		filters.statusFilters.length > 0 && !filters.statusFilters.includes(status);
	return excludedByArchiveToggle || excludedByStatusFilter;
}

/** Would a card in `spaceId` be hidden by the active space filter? Empty filter = all spaces. */
export function isExcludedBySpace(spaceId: string, filters: FeedFilterState): boolean {
	return filters.spaceFilter.length > 0 && !filters.spaceFilter.includes(spaceId || 'default');
}

/** Most-severe priority across a group's cards (the server-computed top_priority). */
export function computeTopPriority(cards: ActionCard[]): CardGroup['top_priority'] {
	return cards.reduce(
		(top, c) => (PRIORITY_RANK[c.priority] < PRIORITY_RANK[top] ? c.priority : top),
		cards[0]?.priority ?? 'MEDIUM'
	) as CardGroup['top_priority'];
}

/** Does a group still hold a card the user could still act on? */
export function computeHasPending(cards: ActionCard[]): boolean {
	return cards.some((c) => c.status === 'pending' || c.status === 'ready');
}

/**
 * Does an update change a field the active sort orders by? If so the group must
 * re-sort / re-section, which is server-computed and needs a refetch.
 */
export function updateAffectsSort(sortBy: string, payload: CardUpdatePayload): boolean {
	return (
		(sortBy === 'priority' && !!payload.priority) ||
		(sortBy === 'persona' && !!payload.persona) ||
		(sortBy === 'status' && !!payload.status)
	);
}

/** Remove a card from every group, dropping any group left empty. */
export function removeCardFromGroups(groups: CardGroup[], cardId: string): CardGroup[] {
	return groups
		.map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_id !== cardId) }))
		.filter((g) => g.cards.length > 0);
}

/**
 * Apply a payload to a single card, returning a NEW card plus whether it was an
 * agent-running card (whose terminal transition warrants a full reload).
 *
 * NOTE the quirk preserved from the original: header/summary/selected_action_id
 * are only applied when `status` is also present in the payload — the backend
 * only sends those together. persona/has_workspace/bookmarked_at apply
 * unconditionally, and bookmarked_at uses `in` so it can be explicitly cleared.
 */
export function applyPayloadToCard(
	card: ActionCard,
	payload: CardUpdatePayload
): { card: ActionCard; wasAgent: boolean } {
	const next = { ...card };
	let wasAgent = false;
	if (payload.status) {
		wasAgent = card.status === 'agent_running';
		next.status = payload.status as ActionCard['status'];
		if (payload.header) next.header = payload.header;
		if (payload.summary) next.summary = payload.summary;
		if (payload.selected_action_id) next.selected_action_id = payload.selected_action_id;
	}
	if (payload.priority) next.priority = payload.priority as ActionCard['priority'];
	if (payload.persona) next.persona = payload.persona as ActionCard['persona'];
	if (payload.has_workspace !== undefined) next.has_workspace = payload.has_workspace;
	if ('bookmarked_at' in payload) next.bookmarked_at = payload.bookmarked_at ?? undefined;
	if (payload.space_id !== undefined) next.space_id = payload.space_id;
	return { card: next, wasAgent };
}

/**
 * Reduce a `card_updated` message against the current feed state.
 *
 * Returns the next `groups` + `selectedCard` and the effects the component must
 * run. Behavior is a faithful port of the original inline handler:
 *  - New status hidden by filters → keep groups as-is and emit an exit/remove
 *    effect (the fade-out owns the actual removal); deselect if it was open.
 *  - Card present → apply the payload immutably, recompute the group's
 *    server-derived top_priority / has_pending, sync the open detail card, and
 *    reload if an agent card just finished or the change affects the active sort.
 *  - Card absent → reload (it may have been created off-page).
 */
export function reduceCardUpdated(
	groups: CardGroup[],
	selectedCard: ActionCard | null,
	cardId: string,
	payload: CardUpdatePayload,
	filters: FeedFilterState
): CardUpdateResult {
	const effects: CardUpdateEffect[] = [];

	// A card whose new status OR new space the active filters exclude leaves the feed.
	// The space case fires when a card is moved out of the currently-viewed space
	// (move-to-space) — treated identically to a filtered-out status change.
	const statusExcluded = !!payload.status && isExcludedByFilters(payload.status, filters);
	const spaceExcluded = payload.space_id !== undefined && isExcludedBySpace(payload.space_id, filters);
	if (statusExcluded || spaceExcluded) {
		effects.push({ kind: 'removeFromSelection', cardId });
		let nextSelected = selectedCard;
		if (selectedCard?.card_id === cardId) {
			nextSelected = null;
			effects.push({ kind: 'deselect' });
		}
		effects.push({ kind: 'exitThenRemove', cardId });
		// Groups are untouched here — the exit transition removes the card later.
		return { groups, selectedCard: nextSelected, effects };
	}

	let found = false;
	let nextSelected = selectedCard;
	const nextGroups = groups.map((group) => {
		const idx = group.cards.findIndex((c) => c.card_id === cardId);
		if (idx < 0) return group;
		found = true;

		const { card: updatedCard, wasAgent } = applyPayloadToCard(group.cards[idx], payload);
		const cards = [...group.cards];
		cards[idx] = updatedCard;
		const nextGroup: CardGroup = { ...group, cards };

		if (payload.status) {
			nextGroup.has_pending = computeHasPending(cards);
			if (wasAgent && payload.status !== 'agent_running') effects.push({ kind: 'reload' });
		}
		if (payload.priority) {
			nextGroup.top_priority = computeTopPriority(cards);
		}
		if (selectedCard?.card_id === cardId) {
			nextSelected = { ...selectedCard, ...updatedCard } as ActionCard;
		}
		return nextGroup;
	});

	if (found) {
		if (updateAffectsSort(filters.sortBy, payload)) effects.push({ kind: 'reload' });
		return { groups: nextGroups, selectedCard: nextSelected, effects };
	}

	// Card not in the current groups — created while off-page or a missed
	// card_created. Reload to pick it up; leave state untouched.
	effects.push({ kind: 'reload' });
	return { groups, selectedCard, effects };
}
