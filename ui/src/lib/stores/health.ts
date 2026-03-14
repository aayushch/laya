import { writable, derived } from 'svelte/store';
import type { HealthResponse } from '$lib/api/types';

const ENGINE_URL = 'http://127.0.0.1:8420';

export const health = writable<HealthResponse | null>(null);
export const healthError = writable<boolean>(false);

/** True once the engine reports healthy (engine + sqlite). Stays true once set. */
export const startupReady = writable<boolean>(false);

let pollInterval: ReturnType<typeof setInterval> | null = null;
let startupMode = true;

async function fetchHealth() {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`);
		if (resp.ok) {
			const data: HealthResponse = await resp.json();
			health.set(data);
			healthError.set(false);

			// Once engine + sqlite are healthy, mark startup as complete
			// and switch to slow polling
			if (startupMode && data.engine === 'healthy' && data.sqlite === 'healthy') {
				startupReady.set(true);
				startupMode = false;
				// Switch to normal 30s polling
				if (pollInterval) {
					clearInterval(pollInterval);
					pollInterval = setInterval(fetchHealth, 30000);
				}
			}
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
	// Poll every 1.5s during startup, switches to 30s once ready
	pollInterval = setInterval(fetchHealth, 1500);
}

export function stopHealthPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
}
