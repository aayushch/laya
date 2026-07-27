// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// "What is still burning right now?" — the attention state of one entity thread.
// Kept a pure function of the thread's cards so the rules can be tuned (and
// unit-tested) without touching any rendering code.

import type { ActionCard } from '$lib/api/types';
import { parseBackendDate } from '$lib/utils/datetime';

/** Statuses that end a thread — nothing terminal can be escalating. */
export const TERMINAL_STATUSES = new Set(['done', 'dismissed', 'archived']);

export interface ThreadAttention {
	escalating: boolean;
	agentRunning: boolean;
	awaitingInput: boolean;
	/** Non-terminal and asking something of the user (the "N awaiting you" chip). */
	needsYou: boolean;
	/** Which rule fired, for the tooltip and for debugging tuning changes. */
	reason: string;
}

export interface AttentionOptions {
	/** Reference "now" — injected so tests aren't wall-clock dependent. */
	now?: Date;
	/** Hours an unanswered follow-up chain may age before it escalates. */
	staleHours?: number;
	/** Hours a CRITICAL thread may stay open before it escalates. */
	criticalHours?: number;
	/** Events a thread needs before ageing counts as "unanswered follow-ups". */
	minStaleEvents?: number;
}

/** Only these priorities can escalate on age alone (see the stale rule below). */
const LOUD_PRIORITIES = new Set(['HIGH', 'CRITICAL']);

const HOUR_MS = 3600_000;

/**
 * A thread escalates when any of these holds:
 *   (a) its latest card failed;
 *   (b) it is HIGH/CRITICAL, has `minStaleEvents`+ events, is still unactioned,
 *       and has aged past `staleHours` — the "3rd follow-up, still unanswered"
 *       case the design is built around;
 *   (c) it is CRITICAL, still open, and older than `criticalHours`.
 *
 * The priority gate in (b) matters: without it, ageing alone flags every
 * unactioned MEDIUM/LOW thread on any day you're not actively working, which
 * turns the whole timeline red and makes the signal worthless (measured at
 * 62 of 121 threads on a real day before the gate went in).
 *
 * The handoff also lists an SLA/due-date rule. Laya cards carry no due-date
 * field today (it lives on the source event's metadata, which the feed never
 * loads), so that rule is deliberately absent rather than faked — add it here
 * when a due field reaches the card model.
 */
export function threadAttention(cards: ActionCard[], opts: AttentionOptions = {}): ThreadAttention {
	const now = opts.now ?? new Date();
	const staleHours = opts.staleHours ?? 4;
	const criticalHours = opts.criticalHours ?? 6;
	const minStaleEvents = opts.minStaleEvents ?? 3;

	const none: ThreadAttention = {
		escalating: false,
		agentRunning: false,
		awaitingInput: false,
		needsYou: false,
		reason: ''
	};
	if (cards.length === 0) return none;

	const sorted = [...cards].sort(
		(a, b) => (parseBackendDate(a.created_at)?.getTime() ?? 0) - (parseBackendDate(b.created_at)?.getTime() ?? 0)
	);
	const latest = sorted[sorted.length - 1];
	const first = sorted[0];

	const agentRunning = cards.some((c) => c.status === 'agent_running');
	const awaitingInput = cards.some(
		(c) => c.status === 'awaiting_input' || (c.has_workspace && !TERMINAL_STATUSES.has(c.status) && c.status !== 'agent_running')
	);

	const openCards = cards.filter((c) => !TERMINAL_STATUSES.has(c.status));
	const needsYou = openCards.some((c) => c.status === 'ready' || c.status === 'awaiting_input');

	const latestAgeH = (now.getTime() - (parseBackendDate(latest.created_at)?.getTime() ?? now.getTime())) / HOUR_MS;
	const openAgeH = (now.getTime() - (parseBackendDate(first.created_at)?.getTime() ?? now.getTime())) / HOUR_MS;
	const topPriority = cards.some((c) => c.priority === 'CRITICAL') ? 'CRITICAL' : latest.priority;

	let escalating = false;
	let reason = '';
	if (latest.status === 'failed') {
		escalating = true;
		reason = 'Latest event failed';
	} else if (
		LOUD_PRIORITIES.has(topPriority) &&
		openCards.length > 0 &&
		cards.length >= minStaleEvents &&
		latestAgeH > staleHours
	) {
		escalating = true;
		reason = `Unanswered for ${Math.floor(latestAgeH)}h across ${cards.length} events`;
	} else if (topPriority === 'CRITICAL' && openCards.length > 0 && openAgeH > criticalHours) {
		escalating = true;
		reason = `Critical and open ${Math.floor(openAgeH)}h`;
	}

	if (!reason) {
		if (agentRunning) reason = 'Agent running';
		else if (awaitingInput) reason = 'Awaiting your input';
	}

	return { escalating, agentRunning, awaitingInput, needsYou, reason };
}
