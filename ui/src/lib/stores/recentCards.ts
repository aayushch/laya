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
			visited_at: Date.now()
		};
		const filtered = entries.filter((e) => e.card_id !== card.card_id);
		const next = [entry, ...filtered].slice(0, MAX_ENTRIES);
		saveToStorage(next);
		return next;
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
