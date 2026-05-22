// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function getInitial(): boolean {
	if (!browser) return false;
	const stored = localStorage.getItem('laya-reduced-motion');
	if (stored !== null) return stored === 'true';
	// First run: honor OS-level prefers-reduced-motion as the default
	return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

const { subscribe, set } = writable<boolean>(getInitial());

export const reducedMotion = {
	subscribe,
	set(value: boolean) {
		set(value);
		if (browser) localStorage.setItem('laya-reduced-motion', String(value));
	}
};
