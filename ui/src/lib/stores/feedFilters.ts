import { writable, get } from 'svelte/store';
import { engineApi } from '$lib/api/engine';

export interface FeedFilters {
	statusFilters: string[];
	priorityFilters: string[];
	sortBy: string;
	showArchived: boolean;
}

/** Shared date navigation state (not persisted — resets to today on reload). */
export const feedDate = writable<string>(new Date().toISOString().slice(0, 10));
export const feedPrevDate = writable<string | null>(null);
export const feedNextDate = writable<string | null>(null);

const defaults: FeedFilters = {
	statusFilters: [],
	priorityFilters: [],
	sortBy: 'newest',
	showArchived: false
};

export const feedFilters = writable<FeedFilters>({ ...defaults });

let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _loaded = false;

/** Load feed preferences from the engine settings API. */
export async function loadFeedFilters(): Promise<void> {
	try {
		const settings = await engineApi.getSettings();
		const prefs = settings.feed_preferences as Partial<FeedFilters> | undefined;
		if (prefs) {
			feedFilters.set({
				statusFilters: prefs.statusFilters ?? defaults.statusFilters,
				priorityFilters: prefs.priorityFilters ?? defaults.priorityFilters,
				sortBy: prefs.sortBy ?? defaults.sortBy,
				showArchived: prefs.showArchived ?? defaults.showArchived
			});
		}
		_loaded = true;
	} catch {
		// Engine not ready yet — use defaults
		_loaded = true;
	}
}

/** Persist current filter values to the engine settings API (debounced). */
export function saveFeedFilters(): void {
	if (!_loaded) return;
	if (_saveTimer) clearTimeout(_saveTimer);
	_saveTimer = setTimeout(async () => {
		_saveTimer = null;
		try {
			const current = get(feedFilters);
			await engineApi.updateSettings({ feed_preferences: current } as Partial<import('$lib/api/types').Settings>);
		} catch {
			// Silently fail — don't disrupt UX
		}
	}, 500);
}
