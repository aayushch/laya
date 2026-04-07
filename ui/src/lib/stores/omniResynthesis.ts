import { writable } from 'svelte/store';

/**
 * Tracks which spaces currently have a resynthesis in progress.
 * Survives SPA navigation (in-memory store), resets on full page reload.
 */
export const resynthesizingSpaces = writable<Set<string>>(new Set());

export function markResynthesizing(spaceId: string) {
	resynthesizingSpaces.update((s) => {
		const next = new Set(s);
		next.add(spaceId);
		return next;
	});
}

export function clearResynthesizing(spaceId: string) {
	resynthesizingSpaces.update((s) => {
		const next = new Set(s);
		next.delete(spaceId);
		return next;
	});
}
