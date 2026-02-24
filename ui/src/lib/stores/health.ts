import { writable } from 'svelte/store';
import type { HealthResponse } from '$lib/api/types';

const ENGINE_URL = 'http://127.0.0.1:8420';

export const health = writable<HealthResponse | null>(null);
export const healthError = writable<boolean>(false);

let pollInterval: ReturnType<typeof setInterval> | null = null;

async function fetchHealth() {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`);
		if (resp.ok) {
			const data: HealthResponse = await resp.json();
			health.set(data);
			healthError.set(false);
		} else {
			healthError.set(true);
		}
	} catch {
		health.set(null);
		healthError.set(true);
	}
}

export function startHealthPolling(intervalMs = 10000) {
	fetchHealth(); // immediate first check
	pollInterval = setInterval(fetchHealth, intervalMs);
}

export function stopHealthPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
}
