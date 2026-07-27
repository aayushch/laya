// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type FeedViewMode = 'card' | 'list' | 'timeline';

const STORAGE_KEY = 'laya-feed-view';

const MODES: FeedViewMode[] = ['card', 'list', 'timeline'];

const stored = browser ? localStorage.getItem(STORAGE_KEY) : null;
const initial: FeedViewMode = MODES.includes(stored as FeedViewMode) ? (stored as FeedViewMode) : 'card';

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

