import { writable } from 'svelte/store';

/** Whether the feed is showing the summary view (true) or card view (false) */
export const showSummary = writable(false);
