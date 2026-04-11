import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type FeedViewMode = 'card' | 'list';

const STORAGE_KEY = 'laya-feed-view';

const stored = browser ? localStorage.getItem(STORAGE_KEY) : null;
const initial: FeedViewMode = (stored === 'card' || stored === 'list') ? stored : 'card';

const { subscribe, set: _set } = writable<FeedViewMode>(initial);

export const feedViewMode = {
	subscribe,
	set(value: FeedViewMode) {
		_set(value);
		if (browser) localStorage.setItem(STORAGE_KEY, value);
	},
	update(fn: (val: FeedViewMode) => FeedViewMode) {
		let current: FeedViewMode = 'card';
		subscribe((v) => (current = v))();
		const next = fn(current);
		feedViewMode.set(next);
	}
};

