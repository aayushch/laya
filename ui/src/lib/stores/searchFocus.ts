// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';

export const searchFocusSignal = writable(0);
export const feedSearchQuery = writable('');

export function triggerSearchFocus() {
	searchFocusSignal.update(v => v + 1);
}
