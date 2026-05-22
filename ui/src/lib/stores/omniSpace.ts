// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const KEY = 'laya-omni-space';

function readInitial(): string {
	if (!browser) return 'default';
	return localStorage.getItem(KEY) || 'default';
}

const { subscribe, set: _set } = writable<string>(readInitial());

export const omniSpace = {
	subscribe,
	set(value: string) {
		_set(value);
		if (browser) localStorage.setItem(KEY, value);
	}
};
