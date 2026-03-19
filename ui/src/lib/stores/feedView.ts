import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type FeedViewMode = 'card' | 'summary' | 'list';

const STORAGE_KEY = 'laya-feed-view';

const initial: FeedViewMode = browser
	? ((localStorage.getItem(STORAGE_KEY) as FeedViewMode) ?? 'card')
	: 'card';

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

/** Backward-compatible derived check */
export const showSummary = {
	subscribe(fn: (val: boolean) => void) {
		return feedViewMode.subscribe((mode) => fn(mode === 'summary'));
	},
	set(val: boolean) {
		feedViewMode.set(val ? 'summary' : 'card');
	},
	update(fn: (val: boolean) => boolean) {
		let current = false;
		showSummary.subscribe((v) => (current = v))();
		feedViewMode.set(fn(current) ? 'summary' : 'card');
	}
};
