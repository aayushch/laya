// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';

const STORAGE_KEY = 'laya_recent_cards';
const MAX_ENTRIES = 30;

export interface RecentCardEntry {
	card_id: string;
	header: string;
	summary: string;
	source_ref?: string;
	entity_id?: string;
	category?: string;
	space_id?: string;
	space_name?: string;
	visited_at: number; // epoch ms
	type?: 'card' | 'group';
	card_count?: number;
}

function loadFromStorage(): RecentCardEntry[] {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return [];
		return JSON.parse(raw) as RecentCardEntry[];
	} catch {
		return [];
	}
}

function saveToStorage(entries: RecentCardEntry[]) {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
	} catch {
		// localStorage full or unavailable — silently ignore
	}
}

const initial = loadFromStorage();
const { subscribe, update } = writable<RecentCardEntry[]>(initial);

/** Track a card visit. Deduplicates by card_id (moves existing to top). */
export function trackCardVisit(card: {
	card_id: string;
	header: string;
	summary: string;
	source_ref?: string;
	entity_id?: string;
	category?: string;
	space_id?: string;
	space_name?: string;
	type?: 'card' | 'group';
	card_count?: number;
}) {
	update((entries) => {
		const existing = entries.find((e) => e.card_id === card.card_id);
		// If visited within last 60s (same interaction flow), just update timestamp
		if (existing && Date.now() - existing.visited_at < 60_000) {
			existing.visited_at = Date.now();
			// Update fields in case they changed
			existing.header = card.header;
			existing.summary = card.summary;
			existing.source_ref = card.source_ref;
			existing.entity_id = card.entity_id;
			existing.category = card.category;
			existing.space_id = card.space_id;
			existing.space_name = card.space_name;
			existing.type = card.type;
			existing.card_count = card.card_count;
			const updated = [existing, ...entries.filter((e) => e.card_id !== card.card_id)];
			saveToStorage(updated);
			return updated;
		}
		const entry: RecentCardEntry = {
			card_id: card.card_id,
			header: card.header,
			summary: card.summary,
			source_ref: card.source_ref,
			entity_id: card.entity_id,
			category: card.category,
			space_id: card.space_id,
			space_name: card.space_name,
			visited_at: Date.now(),
			type: card.type,
			card_count: card.card_count
		};
		const filtered = entries.filter((e) => e.card_id !== card.card_id);
		const next = [entry, ...filtered].slice(0, MAX_ENTRIES);
		saveToStorage(next);
		return next;
	});
}

/** Track a group visit. Uses entity_id as the dedup key. */
export function trackGroupVisit(group: {
	entity_id: string;
	entity_title: string;
	platform: string;
	card_count: number;
	group_summary?: { headline: string; summary: string } | null;
	space_id?: string;
	space_name?: string;
}) {
	trackCardVisit({
		card_id: group.entity_id,
		header: group.entity_title,
		summary: group.group_summary?.headline ?? '',
		source_ref: group.platform,
		entity_id: group.entity_id,
		space_id: group.space_id,
		space_name: group.space_name,
		type: 'group',
		card_count: group.card_count
	});
}

export function clearRecentCards() {
	update(() => {
		saveToStorage([]);
		return [];
	});
}

export const recentCards = { subscribe };

/** Whether the recent-cards drawer is open */
export const recentDrawerOpen = writable(false);
