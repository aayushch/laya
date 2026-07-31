// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Shared vocabulary for the Omni board: the four compression layers, priority
// ranking, and the compact time formats the instruments and rows use.
//
// The layer order here is the compression chain itself — `attention → recent →
// period → milestone → gone`. It mirrors SECTION_CHAIN in the engine's
// pipeline/omni_change.py; the two must stay in step or a fold annotation will
// point the wrong way.

import type { OmniSectionType } from '$lib/api/types';
import { parseBackendDate } from '$lib/utils/datetime';

export interface LayerMeta {
	type: OmniSectionType;
	title: string;
	/** Time window shown beside the band title. */
	window: string;
	/** CSS var suffix — var(--om-layer-<token>) / var(--om-layer-<token>-fg). */
	token: string;
	/** Funnel band width. The narrowing IS the idea: content compresses downward. */
	width: string;
}

export const LAYERS: LayerMeta[] = [
	{ type: 'attention', title: 'Needs Attention', window: 'OPEN NOW', token: 'attention', width: '100%' },
	{ type: 'recent', title: 'Recent', window: 'LAST 24–48H', token: 'recent', width: '89%' },
	{ type: 'period', title: 'This Week', window: 'MON–TODAY', token: 'period', width: '75%' },
	{ type: 'milestone', title: 'Milestones', window: 'BEYOND', token: 'milestone', width: '60%' }
];

export const LAYER_BY_TYPE: Record<string, LayerMeta> = Object.fromEntries(
	LAYERS.map((l) => [l.type, l])
);

/** Uppercase name used in changelog meta lines ("RECENT → THIS WEEK"). */
export function layerLabel(type: string | null | undefined): string {
	if (!type) return '';
	return (LAYER_BY_TYPE[type]?.title ?? type).toUpperCase();
}

// --- Priority ---

export const PRIORITY_RANK: Record<string, number> = {
	CRITICAL: 0,
	HIGH: 1,
	MEDIUM: 2,
	LOW: 3
};

/** CSS var suffix for a priority: var(--om-pri-<token>-bg/-fg), var(--om-bar-<token>). */
export function priorityToken(priority: string | null | undefined): string {
	const p = (priority ?? 'MEDIUM').toUpperCase();
	return p in PRIORITY_RANK ? p.toLowerCase() : 'medium';
}

/**
 * The priority to *display*. `item.priority` is frozen at synthesis time, so a
 * subject that has since been merged would still shout CRITICAL; the live value
 * (highest priority among non-terminal source cards) is the honest one. Falls
 * back to the frozen value when nothing live is known.
 */
export function livePriority(item: {
	priority: string;
	live?: { max_priority: string | null } | undefined;
}): string {
	return item.live?.max_priority ?? item.priority ?? 'MEDIUM';
}

// --- Compact time formats ---

/** "2h" / "5d" / "20m" — the triage column's age slot. */
export function shortAge(iso: string | null | undefined, now: number = Date.now()): string {
	const d = parseBackendDate(iso);
	if (!d) return '';
	const mins = Math.max(0, Math.floor((now - d.getTime()) / 60000));
	if (mins < 1) return 'now';
	if (mins < 60) return `${mins}m`;
	const hours = Math.floor(mins / 60);
	if (hours < 48) return `${hours}h`;
	return `${Math.floor(hours / 24)}d`;
}

/** "3h 12m" / "45m" — spans and countdowns. Empty when the span is unknown. */
export function duration(ms: number | null | undefined): string {
	if (ms == null || !Number.isFinite(ms) || ms <= 0) return '';
	const mins = Math.floor(ms / 60000);
	if (mins < 1) return 'under a minute';
	if (mins < 60) return `${mins}m`;
	const hours = Math.floor(mins / 60);
	if (hours < 24) return `${hours}h ${mins % 60}m`;
	const days = Math.floor(hours / 24);
	return `${days}d ${hours % 24}h`;
}

/** Countdown to a future timestamp, or 'imminent' once it has passed. */
export function countdownTo(iso: string | null | undefined, now: number = Date.now()): string {
	const d = parseBackendDate(iso);
	if (!d) return '';
	const diff = d.getTime() - now;
	if (diff <= 60000) return 'imminent';
	return duration(diff);
}

/** "7:27 PM" — the identity bar's snapshot stamp. */
export function clockTime(iso: string | null | undefined): string {
	const d = parseBackendDate(iso);
	if (!d) return '';
	return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** "14:22" — 24h stamp for changelog "closed" lines and evidence rows. */
export function hhmm(iso: string | null | undefined): string {
	const d = parseBackendDate(iso);
	if (!d) return '';
	return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** Thousands-separated integer for the numeral columns. */
export function num(value: number | null | undefined): string {
	return (value ?? 0).toLocaleString();
}
