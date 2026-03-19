import { writable } from 'svelte/store';

export type FeedViewMode = 'card' | 'summary' | 'list';

/** Current feed view mode */
export const feedViewMode = writable<FeedViewMode>('card');

/** Backward-compatible derived check */
export const showSummary = {
	subscribe(fn: (val: boolean) => void) {
		return feedViewMode.subscribe((mode) => fn(mode === 'summary'));
	},
	set(val: boolean) {
		feedViewMode.set(val ? 'summary' : 'card');
	},
	update(fn: (val: boolean) => boolean) {
		feedViewMode.update((mode) => fn(mode === 'summary') ? 'summary' : 'card');
	}
};
