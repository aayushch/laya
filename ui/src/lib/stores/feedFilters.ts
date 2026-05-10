import { writable, get } from 'svelte/store';
import { engineApi } from '$lib/api/engine';

export interface FeedFilters {
	statusFilters: string[];
	priorityFilters: string[];
	sortBy: string;
	sortAsc: boolean;
	showArchived: boolean;
	showBookmarked: boolean;
	hasWorkspace: boolean;
	showUnreadOnly: boolean;
	spaceFilter: string[];
	// Transient (not persisted) — related cards filter mode
	showRelated: boolean;
	relatedEntityIds: string[];
	relatedSourceHeader: string;
	relatedSourceCardId: string;
	// Transient (not persisted) — search across all days mode
	showAllDaysSearch: boolean;
}

/** Local date in YYYY-MM-DD format (respects the user's timezone, unlike toISOString which is UTC). */
export function localToday(): string {
	const d = new Date();
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Shared date navigation state (not persisted — resets to today on reload). */
export const feedDate = writable<string>(localToday());
export const feedPrevDate = writable<string | null>(null);
export const feedNextDate = writable<string | null>(null);

/** Saved date before entering all-days search mode (restored on exit). */
export const allDaysSavedDate = writable<string>('');

const defaults: FeedFilters = {
	statusFilters: [],
	priorityFilters: [],
	sortBy: 'newest',
	sortAsc: false,
	showArchived: false,
	showBookmarked: false,
	hasWorkspace: false,
	showUnreadOnly: false,
	spaceFilter: [],
	showRelated: false,
	relatedEntityIds: [],
	relatedSourceHeader: '',
	relatedSourceCardId: '',
	showAllDaysSearch: false
};

export const feedFilters = writable<FeedFilters>({ ...defaults });

let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _loaded = false;

/** True once filters have been loaded from the engine at least once. */
export function filtersLoaded(): boolean {
	return _loaded;
}

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
				sortAsc: prefs.sortAsc ?? defaults.sortAsc,
				showArchived: prefs.showArchived ?? defaults.showArchived,
				showBookmarked: prefs.showBookmarked ?? defaults.showBookmarked,
				hasWorkspace: prefs.hasWorkspace ?? defaults.hasWorkspace,
				showUnreadOnly: prefs.showUnreadOnly ?? defaults.showUnreadOnly,
				spaceFilter: Array.isArray(prefs.spaceFilter) ? prefs.spaceFilter : prefs.spaceFilter ? [prefs.spaceFilter] : defaults.spaceFilter,
				showRelated: false,
				relatedEntityIds: [],
				relatedSourceHeader: '',
				relatedSourceCardId: '',
				showAllDaysSearch: false
			});
		}
		_loaded = true;
	} catch {
		// Engine not ready yet — use defaults but don't mark as loaded
		// so we don't overwrite saved preferences with defaults
	}
}

/** Persist current filter values to the engine settings API (debounced). */
export function saveFeedFilters(): void {
	if (!_loaded) return;
	if (_saveTimer) clearTimeout(_saveTimer);
	_saveTimer = setTimeout(async () => {
		_saveTimer = null;
		try {
			const { showRelated, relatedEntityIds, relatedSourceHeader, relatedSourceCardId, showAllDaysSearch, ...persistable } = get(feedFilters);
			await engineApi.updateSettings({ feed_preferences: persistable } as Partial<import('$lib/api/types').Settings>);
		} catch {
			// Silently fail — don't disrupt UX
		}
	}, 500);
}
