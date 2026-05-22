// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const initial: boolean = browser
	? (localStorage.getItem('laya-accessible-colors') ?? 'false') === 'true'
	: false;

const { subscribe, set } = writable<boolean>(initial);

export const accessibleColors = {
	subscribe,
	set(value: boolean) {
		set(value);
		if (browser) localStorage.setItem('laya-accessible-colors', String(value));
	}
};
