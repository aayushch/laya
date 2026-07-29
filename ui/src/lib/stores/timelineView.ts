// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Timeline ("Day Column") view preferences — localStorage-backed like feedView.
// The time BRUSH deliberately lives in feedFilters instead: it narrows the card
// set the same way status/priority do, and must survive a switch back to
// card/list view.

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

/** Pixels per hour on the linear part of the scale. Higher = more zoomed in. */
export type TimelineZoom = 120 | 60 | 30 | 15;

/** Zoom steps, coarse → fine, labelled by the wall-clock span 60px covers. */
export const ZOOM_STEPS: { hourPx: TimelineZoom; label: string }[] = [
	{ hourPx: 15, label: '4h' },
	{ hourPx: 30, label: '2h' },
	{ hourPx: 60, label: '1h' },
	{ hourPx: 120, label: '30m' }
];

export interface TimelineViewState {
	hourPx: TimelineZoom;
	/** Default collapse state for newly-detected quiet runs. */
	quietCollapsed: boolean;
	laneCount: number;
	/** Overflow strip clicked open into an extra band of lanes. */
	overflowExpanded: boolean;
}

const STORAGE_KEY = 'laya-timeline-view';

const defaults: TimelineViewState = {
	hourPx: 60,
	quietCollapsed: true,
	laneCount: 9,
	overflowExpanded: false
};

function load(): TimelineViewState {
	if (!browser) return { ...defaults };
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return { ...defaults };
		const parsed = JSON.parse(raw) as Partial<TimelineViewState>;
		const hourPx = ZOOM_STEPS.some((s) => s.hourPx === parsed.hourPx)
			? (parsed.hourPx as TimelineZoom)
			: defaults.hourPx;
		return {
			hourPx,
			quietCollapsed: parsed.quietCollapsed ?? defaults.quietCollapsed,
			// Clamped to the tweakable 5–12 band the packing algorithm is tuned for.
			laneCount: Math.min(12, Math.max(5, parsed.laneCount ?? defaults.laneCount)),
			overflowExpanded: parsed.overflowExpanded ?? defaults.overflowExpanded
		};
	} catch {
		return { ...defaults };
	}
}

const { subscribe, set: _set, update: _update } = writable<TimelineViewState>(load());

function persist(value: TimelineViewState) {
	if (browser) localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

export const timelineView = {
	subscribe,
	set(value: TimelineViewState) {
		_set(value);
		persist(value);
	},
	update(fn: (v: TimelineViewState) => TimelineViewState) {
		_update((current) => {
			const next = fn(current);
			persist(next);
			return next;
		});
	},
	/** Step the zoom one notch; `dir` +1 zooms in (more px per hour). */
	zoomBy(dir: 1 | -1) {
		timelineView.update((v) => {
			const idx = ZOOM_STEPS.findIndex((s) => s.hourPx === v.hourPx);
			const next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, idx + dir));
			return { ...v, hourPx: ZOOM_STEPS[next].hourPx };
		});
	},
	setQuietCollapsed(collapsed: boolean) {
		timelineView.update((v) => ({ ...v, quietCollapsed: collapsed }));
	},
	setOverflowExpanded(expanded: boolean) {
		timelineView.update((v) => ({ ...v, overflowExpanded: expanded }));
	}
};

/** Label for the current zoom, e.g. '1h'. */
export function zoomLabel(hourPx: number): string {
	return ZOOM_STEPS.find((s) => s.hourPx === hourPx)?.label ?? '1h';
}
