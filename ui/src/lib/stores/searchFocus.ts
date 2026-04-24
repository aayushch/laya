import { writable } from 'svelte/store';

export const searchFocusSignal = writable(0);

export function triggerSearchFocus() {
	searchFocusSignal.update(v => v + 1);
}
