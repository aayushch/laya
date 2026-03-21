import { writable, get } from 'svelte/store';

const selectedIds = writable<Set<string>>(new Set());
let lastClickedId: string | null = null;

export const feedSelection = {
	subscribe: selectedIds.subscribe,

	toggleCard(cardId: string) {
		selectedIds.update((s) => {
			const next = new Set(s);
			if (next.has(cardId)) next.delete(cardId);
			else next.add(cardId);
			return next;
		});
	},

	selectMany(cardIds: string[]) {
		selectedIds.update((s) => {
			const next = new Set(s);
			for (const id of cardIds) next.add(id);
			return next;
		});
	},

	deselectMany(cardIds: string[]) {
		selectedIds.update((s) => {
			const next = new Set(s);
			for (const id of cardIds) next.delete(id);
			return next;
		});
	},

	deselectAll() {
		selectedIds.set(new Set());
		lastClickedId = null;
	},

	removeDeleted(cardId: string) {
		selectedIds.update((s) => {
			if (!s.has(cardId)) return s;
			const next = new Set(s);
			next.delete(cardId);
			return next;
		});
	},

	has(cardId: string): boolean {
		return get(selectedIds).has(cardId);
	},

	setLastClicked(cardId: string) {
		lastClickedId = cardId;
	},

	getLastClicked(): string | null {
		return lastClickedId;
	}
};
