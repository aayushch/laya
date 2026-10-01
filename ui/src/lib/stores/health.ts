// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import type { HealthResponse, ModelAvailability, WsMessage } from '$lib/api/types';
import { getEngineUrl } from '$lib/config';
import { vectorStoreState } from '$lib/utils/vectorStore';

const ENGINE_URL = getEngineUrl();

const FAST_POLL_MS = 1500;
const SLOW_POLL_MS = 30000;

export const health = writable<HealthResponse | null>(null);
export const healthError = writable<boolean>(false);

/** True once the engine reports healthy (engine + sqlite). Stays true once set. */
export const startupReady = writable<boolean>(false);

let pollInterval: ReturnType<typeof setInterval> | null = null;
let pollMs = FAST_POLL_MS;
let startupMode = true;

function setPollRate(ms: number) {
	if (!pollInterval || ms === pollMs) return;
	clearInterval(pollInterval);
	pollMs = ms;
	pollInterval = setInterval(fetchHealth, ms);
}

async function fetchHealth() {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`);
		if (resp.ok) {
			const data: HealthResponse = await resp.json();
			health.set(data);
			healthError.set(false);

			// Once engine + sqlite are healthy, mark startup as complete
			if (startupMode && data.engine === 'healthy' && data.sqlite === 'healthy') {
				startupReady.set(true);
				startupMode = false;
			}

			// Poll fast during startup and while the vector store is still
			// connecting, so the UI reflects its completion promptly; otherwise
			// poll slowly.
			const settling = startupMode || vectorStoreState(data) === 'starting';
			setPollRate(settling ? FAST_POLL_MS : SLOW_POLL_MS);
		} else {
			healthError.set(true);
		}
	} catch {
		health.set(null);
		healthError.set(true);
	}
}

export function startHealthPolling() {
	stopHealthPolling();
	startupMode = true;
	fetchHealth(); // immediate first check
	pollMs = FAST_POLL_MS;
	pollInterval = setInterval(fetchHealth, pollMs);
}

/** Apply a `model_availability` WS push to the health store. */
export function handleModelAvailabilityWs(msg: WsMessage): void {
	const payload = msg.payload as unknown as ModelAvailability | undefined;
	if (!Array.isArray(payload?.unavailable)) return;
	health.update((h) => (h ? { ...h, models: payload } : h));
}

export function stopHealthPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
}
