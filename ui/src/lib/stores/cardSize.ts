import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type CardSize = 'compact' | 'relaxed';

const KEY = 'laya-card-size';

function readInitial(): CardSize {
	if (!browser) return 'relaxed';
	const v = localStorage.getItem(KEY);
	return v === 'compact' ? 'compact' : 'relaxed';
}

const { subscribe, set, update } = writable<CardSize>(readInitial());

function persist(value: CardSize) {
	if (browser) localStorage.setItem(KEY, value);
}

export const cardSize = {
	subscribe,
	set(value: CardSize) {
		set(value);
		persist(value);
	},
	toggle() {
		update((v) => {
			const next: CardSize = v === 'compact' ? 'relaxed' : 'compact';
			persist(next);
			return next;
		});
	}
};
