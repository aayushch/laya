// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const initial: boolean = browser
	? (localStorage.getItem('laya-glass-theme') ?? 'true') === 'true'
	: true;

const { subscribe, set } = writable<boolean>(initial);

export const glassTheme = {
	subscribe,
	set(value: boolean) {
		set(value);
		if (browser) localStorage.setItem('laya-glass-theme', String(value));
	}
};
