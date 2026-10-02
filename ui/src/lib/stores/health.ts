// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import type { HealthResponse } from '$lib/api/types';
import { getEngineUrl, waitForEngineToken } from '$lib/config';
import { vectorStoreState } from '$lib/utils/vectorStore';

const ENGINE_URL = getEngineUrl();

const FAST_POLL_MS = 1500;
const SLOW_POLL_MS = 30000;

export const health = writable<HealthResponse | null>(null);
export const healthError = writable<boolean>(false);

/** True once the engine reports healthy (engine + sqlite) AND token is available. Stays true once set. */
export const startupReady = writable<boolean>(false);

let pollInterval: ReturnType<typeof setInterval> | null = null;
let pollMs = FAST_POLL_MS;
let startupMode = true;
let engineReadyPromise: Promise<void> | null = null;

function setPollRate(ms: number) {
	if (!pollInterval || ms === pollMs) return;
	clearInterval(pollInterval);
	pollMs = ms;
	pollInterval = setInterval(fetchHealth, ms);
}

export async function fetchHealth(): Promise<HealthResponse | null> {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`);
		if (resp.ok) {
			const data: HealthResponse = await resp.json();
			health.set(data);
			healthError.set(false);

			// Once engine + sqlite are healthy, ensure token is ready before marking startup complete
			if (startupMode && data.engine === 'healthy' && data.sqlite === 'healthy') {
				try {
					await waitForEngineToken();
					startupReady.set(true);
					startupMode = false;
				} catch {
					// Token not available yet; remain in startupMode and retry next poll
				}
			}

			// Poll fast during startup and while the vector store is still
			// connecting, so the UI reflects its completion promptly; otherwise
			// poll slowly.
			const settling = startupMode || vectorStoreState(data) === 'starting';
			setPollRate(settling ? FAST_POLL_MS : SLOW_POLL_MS);
			return data;
		} else {
			healthError.set(true);
			return null;
		}
	} catch {
		health.set(null);
		healthError.set(true);
		return null;
	}
}

/**
 * Ensure engine is ready (token available + healthy).
 * Single shared promise so callers do not race each other.
 */
export function ensureEngineReady(): Promise<void> {
	if (engineReadyPromise) return engineReadyPromise;
	engineReadyPromise = (async () => {
		await waitForEngineToken();
		await fetchHealth();
	})().catch((err) => {
		engineReadyPromise = null;
		throw err;
	});
	return engineReadyPromise;
}

export function startHealthPolling() {
	stopHealthPolling();
	startupMode = true;
	fetchHealth(); // immediate first check
	pollMs = FAST_POLL_MS;
	pollInterval = setInterval(fetchHealth, pollMs);
}

export function stopHealthPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
}
