import { writable } from 'svelte/store';

export const searchFocusSignal = writable(0);
export const feedSearchQuery = writable('');

export function triggerSearchFocus() {
	searchFocusSignal.update(v => v + 1);
}
