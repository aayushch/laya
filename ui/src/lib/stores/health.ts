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

/**
 * Set while the engine is healthy but this page has no engine API token, so
 * startup cannot complete. Holds the message the startup screen shows.
 */
export const engineAuthError = writable<string | null>(null);

const ENGINE_AUTH_ERROR_MESSAGE =
	'Laya could not get its engine access token. Try restarting the app.' +
	(import.meta.env?.DEV
		? " Browser dev mode: set VITE_LAYA_ENGINE_TOKEN in ui/.env.local to the engine's LAYA_ENGINE_TOKEN."
		: '');

const ENGINE_TOKEN_REJECTED_MESSAGE =
	"The Laya engine does not accept this window's access token. Try restarting the app." +
	(import.meta.env?.DEV
		? " Browser dev mode: VITE_LAYA_ENGINE_TOKEN in ui/.env.local must equal the engine's LAYA_ENGINE_TOKEN."
		: '');

/**
 * Startup checks in a row in which the engine refuses this page's token before
 * the startup screen says so (about 15 seconds at the fast poll rate). A
 * refusal for a few seconds is normal after a restart: see engineAcceptsToken.
 */
export const TOKEN_REJECTED_CHECKS_BEFORE_ERROR = 10;

let pollInterval: ReturnType<typeof setInterval> | null = null;
let pollMs = FAST_POLL_MS;
let startupMode = true;
// One token wait at a time: a wait can outlast the poll interval, and
// overlapping polls would otherwise each start their own.
let tokenWaitInFlight = false;
let tokenErrorLogged = false;
let tokenRejectedChecks = 0;

function setPollRate(ms: number) {
	if (!pollInterval || ms === pollMs) return;
	clearInterval(pollInterval);
	pollMs = ms;
	pollInterval = setInterval(fetchHealth, ms);
}

/**
 * True when the engine answering on the port accepts `token`. After the app
 * restarts, the engine from the previous launch can still answer /health for
 * a few seconds while the new one starts. It holds the previous launch's
 * token and would refuse every request this page makes, so startup waits for
 * the engine that accepts this page's token.
 */
async function engineAcceptsToken(token: string): Promise<boolean> {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!resp.ok) return false;
		const data: HealthResponse = await resp.json();
		return data.token === 'accepted';
	} catch {
		return false;
	}
}

async function fetchHealth() {
	try {
		const resp = await fetch(`${ENGINE_URL}/health`);
		if (resp.ok) {
			const data: HealthResponse = await resp.json();
			health.set(data);
			healthError.set(false);

			// Once engine + sqlite are healthy, ensure the token is ready and that
			// this engine accepts it before marking startup complete
			if (
				startupMode &&
				!tokenWaitInFlight &&
				data.engine === 'healthy' &&
				data.sqlite === 'healthy'
			) {
				tokenWaitInFlight = true;
				try {
					const token = await waitForEngineToken();
					if (await engineAcceptsToken(token)) {
						tokenRejectedChecks = 0;
						engineAuthError.set(null);
						startupReady.set(true);
						startupMode = false;
					} else {
						// Stay in startupMode; the next poll checks again.
						tokenRejectedChecks += 1;
						if (tokenRejectedChecks >= TOKEN_REJECTED_CHECKS_BEFORE_ERROR) {
							engineAuthError.set(ENGINE_TOKEN_REJECTED_MESSAGE);
						}
					}
				} catch (err) {
					// Stay in startupMode; the next poll retries.
					if (!tokenErrorLogged) {
						tokenErrorLogged = true;
						console.error('Engine is healthy but no engine API token is available', err);
					}
					engineAuthError.set(ENGINE_AUTH_ERROR_MESSAGE);
				} finally {
					tokenWaitInFlight = false;
				}
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
	tokenRejectedChecks = 0;
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
