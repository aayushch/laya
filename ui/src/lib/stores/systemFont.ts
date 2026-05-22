// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function getInitial(): boolean {
	if (!browser) return false;
	return localStorage.getItem('laya-system-font') === 'true';
}

const { subscribe, set } = writable<boolean>(getInitial());

export const systemFont = {
	subscribe,
	set(value: boolean) {
		set(value);
		if (browser) localStorage.setItem('laya-system-font', String(value));
	}
};
