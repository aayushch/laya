// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import type { Space } from '$lib/api/types';
import { engineApi } from '$lib/api/engine';

export const spaces = writable<Space[]>([]);

let _loaded = false;

/** Load spaces from the engine API. */
export async function loadSpaces(): Promise<void> {
	try {
		const data = await engineApi.getSpaces();
		spaces.set(data.spaces);
		_loaded = true;
	} catch {
		// Engine not ready — keep empty
		_loaded = true;
	}
}

/** Reload spaces (e.g. after creating/deleting). */
export async function refreshSpaces(): Promise<void> {
	_loaded = false;
	await loadSpaces();
}
