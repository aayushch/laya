// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// A "thread" is an entity's lifecycle: the CardGroup from /cards/grouped, read
// as one object that starts at its first event and ends at its last. This maps
// the feed's already-loaded groups onto the timeline's geometry — no extra
// fetch, and clicking a capsule opens exactly the same detail panel the card
// and list views open.

import type { ActionCard, CardGroup } from '$lib/api/types';
import { parseBackendDate } from '$lib/utils/datetime';
import { platformKey } from '$lib/utils/cardVisuals';
import { localMinutes } from './scale';
import { threadAttention, TERMINAL_STATUSES, type ThreadAttention } from '$lib/utils/threadAttention';

export interface ThreadEvent {
	cardId: string;
	card: ActionCard;
	/** Minutes from local midnight of the SELECTED day. */
	minute: number;
	status: string;
	statusLabel: string;
	/** True when the card is from an earlier day and was carried forward. */
	carried: boolean;
}

export interface Thread {
	key: string;
	entityId: string;
	title: string;
	platform: string;
	priority: string;
	persona: string;
	spaceId?: string;
	spaceName?: string;
	spaceColor?: string;
	/** Short entity keys (PR-851, FERR-1585, …), most identifying first. */
	labels: string[];
	events: ThreadEvent[];
	firstMinute: number;
	lastMinute: number;
	/** The thread was already open when the day started. */
	carriedForward: boolean;
	cardCount: number;
	unreadCount: number;
	attention: ThreadAttention;
	latest: ThreadEvent;
	/** Hours between the thread's first and last event on this day. */
	openHours: number;
	group: CardGroup;
	/** Single-card threads open the card detail, not the group summary. */
	singleCard: boolean;
}

export const STATUS_LABELS: Record<string, string> = {
	pending: 'Processing',
	ready: 'Ready',
	agent_running: 'Running',
	awaiting_input: 'Awaiting input',
	executing: 'Executing',
	done: 'Done',
	failed: 'Failed',
	dismissed: 'Dismissed',
	archived: 'Archived'
};

/** Status → the visual family the capsule spine, dots and chip use. */
export function statusTone(status: string): 'ready' | 'running' | 'done' | 'failed' | 'dormant' {
	switch (status) {
		case 'failed':
			return 'failed';
		case 'agent_running':
		case 'awaiting_input':
			return 'running';
		case 'done':
			return 'done';
		case 'pending':
		case 'ready':
		case 'executing':
			return 'ready';
		default:
			return 'dormant';
	}
}

/**
 * The readable tail of an entity id: the last path segment of the last
 * colon-segment. `bitbucket:pull_request:groundlabs/ferret-backend/PR-851`
 * → `PR-851`, `jira:ticket:FERR-1585` → `FERR-1585`. Slack thread ids carry a
 * channel plus a timestamp (`thread-C08JSD56XNE-1785308884.008409`); only the
 * channel is worth the pixels.
 */
export function shortEntityLabel(entityId?: string): string {
	if (!entityId) return '';
	const tail = entityId.split(':').pop() ?? entityId;
	const segment = tail.split('/').filter(Boolean).pop() ?? tail;
	const slackThread = segment.match(/^thread-([A-Za-z0-9]+)-[\d.]+$/);
	const label = slackThread ? slackThread[1] : segment;
	return label.length > 14 ? `${label.slice(0, 13)}…` : label;
}

/**
 * Rank: ticket/PR keys (`PR-851`, `FERR-1585`) first, opaque ids (gmail thread
 * hashes, slack channel ids) last. A context group can hold all three, and the
 * capsule only has room for one or two — so the useful one has to win.
 */
export function labelRank(label: string): number {
	if (/^[A-Za-z]{1,10}[-_ ]?\d+$/.test(label)) return 0;
	if (/^[0-9a-f]{10,}…?$/i.test(label)) return 3;
	if (/\d/.test(label) && /[A-Za-z]/.test(label) && label.length <= 12) return 1;
	return 2;
}

/** Distinct, ranked short labels for a thread's entities (context groups have several). */
export function entityLabels(cards: ActionCard[]): string[] {
	const seen = new Set<string>();
	const labels: string[] = [];
	for (const card of cards) {
		const label = shortEntityLabel(card.entity_id);
		if (!label || seen.has(label)) continue;
		seen.add(label);
		labels.push(label);
	}
	// Stable within a rank so the order still reflects the thread's chronology.
	return labels
		.map((label, i) => ({ label, rank: labelRank(label), i }))
		.sort((a, b) => a.rank - b.rank || a.i - b.i)
		.map((x) => x.label);
}

/** Local YYYY-MM-DD for a Date (matches feedDate, which is a local date). */
function localDateKey(d: Date): string {
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export interface BuildThreadsOptions {
	/** The selected day, 'YYYY-MM-DD' local. */
	date: string;
	now?: Date;
}

/**
 * Map card groups onto threads. Cards created on an EARLIER day (carried
 * forward into this day's feed) keep their real chronology but are pinned to
 * the start of the day — their true time-of-day belongs to a different day's
 * axis, and drawing it here would be a lie.
 */
export function buildThreads(groups: CardGroup[], opts: BuildThreadsOptions): Thread[] {
	const now = opts.now ?? new Date();
	const threads: Thread[] = [];

	for (const group of groups) {
		if (group.cards.length === 0) continue;

		const events: ThreadEvent[] = [];
		for (const card of group.cards) {
			const d = parseBackendDate(card.created_at);
			if (!d) continue;
			const carried = localDateKey(d) !== opts.date;
			events.push({
				cardId: card.card_id,
				card,
				minute: carried ? 0 : localMinutes(d),
				status: card.status,
				statusLabel: STATUS_LABELS[card.status] ?? card.status,
				carried
			});
		}
		if (events.length === 0) continue;

		// Carried events sort first (they happened earlier by definition), then
		// by clock time within the day.
		events.sort((a, b) => Number(b.carried) - Number(a.carried) || a.minute - b.minute);

		const onDay = events.filter((e) => !e.carried);
		const carriedForward = onDay.length !== events.length;
		const firstMinute = onDay.length > 0 ? onDay[0].minute : 0;
		const lastMinute = onDay.length > 0 ? onDay[onDay.length - 1].minute : firstMinute;
		const latest = events[events.length - 1];
		const firstCard = group.cards[0];

		threads.push({
			key: group.entity_id,
			entityId: group.entity_id,
			title: group.entity_title || firstCard.header,
			platform: (group.platform || platformKey(group.entity_id) || '').toLowerCase(),
			priority: group.top_priority,
			persona: firstCard.persona,
			spaceId: firstCard.space_id,
			spaceName: firstCard.space_name,
			spaceColor: firstCard.space_color,
			labels: entityLabels(group.cards),
			events,
			firstMinute,
			lastMinute,
			carriedForward,
			cardCount: group.card_count,
			unreadCount: group.unread_count,
			attention: threadAttention(group.cards, { now }),
			latest,
			openHours: Math.max(0, (lastMinute - firstMinute) / 60),
			group,
			singleCard: group.card_count === 1
		});
	}

	return threads;
}

/** Does this thread have any non-terminal card? (drives the dormant tone) */
export function isThreadOpen(thread: Thread): boolean {
	return thread.group.cards.some((c) => !TERMINAL_STATUSES.has(c.status));
}

/**
 * Attention ticks for the heat rail: one mark per escalating / failed /
 * awaiting-input moment, so they never scroll out of reach.
 */
export interface AttentionMark {
	minute: number;
	kind: 'escalating' | 'agent' | 'needs-you';
	label: string;
	entityId: string;
	spaceName?: string;
	spaceColor?: string;
}

export function attentionMarks(threads: Thread[]): AttentionMark[] {
	const marks: AttentionMark[] = [];
	for (const t of threads) {
		const minute = t.lastMinute;
		const space = { spaceName: t.spaceName, spaceColor: t.spaceColor };
		if (t.attention.escalating) {
			marks.push({ minute, kind: 'escalating', label: `${t.title} — ${t.attention.reason}`, entityId: t.entityId, ...space });
		} else if (t.attention.agentRunning || t.attention.awaitingInput) {
			marks.push({ minute, kind: 'agent', label: `${t.title} — ${t.attention.reason}`, entityId: t.entityId, ...space });
		} else if (t.attention.needsYou) {
			marks.push({ minute, kind: 'needs-you', label: `${t.title} — needs you`, entityId: t.entityId, ...space });
		}
	}
	return marks;
}

/** Overlapping meetings split the calendar rail and turn red. */
export interface MeetingBlock<T> {
	meeting: T;
	startMin: number;
	endMin: number;
	/** Index within its overlapping cluster, and the cluster's size. */
	slot: number;
	slots: number;
}

export function layoutMeetings<T>(
	meetings: { meeting: T; startMin: number; endMin: number }[]
): MeetingBlock<T>[] {
	const sorted = [...meetings].sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
	const out: MeetingBlock<T>[] = [];
	let cluster: MeetingBlock<T>[] = [];
	let clusterEnd = -Infinity;

	const flush = () => {
		for (const block of cluster) block.slots = cluster.length;
		out.push(...cluster);
		cluster = [];
		clusterEnd = -Infinity;
	};

	for (const m of sorted) {
		if (m.startMin >= clusterEnd) flush();
		cluster.push({ ...m, slot: cluster.length, slots: 1 });
		clusterEnd = Math.max(clusterEnd, m.endMin);
	}
	flush();
	return out;
}
