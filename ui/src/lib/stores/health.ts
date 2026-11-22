// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { engineFetch } from '$lib/api/engine';
import type { HealthResponse } from '$lib/api/types';
import { waitForEngineToken } from '$lib/config';
import { vectorStoreState } from '$lib/utils/vectorStore';

const FAST_POLL_MS = 1500;
const SLOW_POLL_MS = 30000;
const HEALTH_TIMEOUT_MS = 5000;

export const health = writable<HealthResponse | null>(null);
export const healthError = writable<boolean>(false);

/** True once a single response confirms engine health, SQLite health and token acceptance. */
export const startupReady = writable<boolean>(false);

/** Startup message for a missing local token or repeated explicit rejections. */
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
 * refusal for a few seconds is normal while the previous engine shuts down.
 */
export const TOKEN_REJECTED_CHECKS_BEFORE_ERROR = 10;

let pollInterval: ReturnType<typeof setInterval> | null = null;
let pollMs = FAST_POLL_MS;
let startupMode = true;
let activePoll: AbortController | null = null;
let tokenErrorLogged = false;
let tokenRejectedChecks = 0;

function recordTokenRejection(rejected: boolean) {
	if (rejected) {
		tokenRejectedChecks += 1;
		if (tokenRejectedChecks >= TOKEN_REJECTED_CHECKS_BEFORE_ERROR) {
			engineAuthError.set(ENGINE_TOKEN_REJECTED_MESSAGE);
		}
		return;
	}
	if (tokenRejectedChecks >= TOKEN_REJECTED_CHECKS_BEFORE_ERROR) {
		engineAuthError.set(null);
	}
	tokenRejectedChecks = 0;
}

function setPollRate(ms: number) {
	if (!pollInterval || ms === pollMs) return;
	clearInterval(pollInterval);
	pollMs = ms;
	pollInterval = setInterval(fetchHealth, ms);
}

async function fetchHealth() {
	if (activePoll) return;
	const poll = new AbortController();
	activePoll = poll;
	let timeout: ReturnType<typeof setTimeout> | undefined;
	try {
		let token: string | undefined;
		let tokenError: unknown;
		if (startupMode) {
			try {
				token = await waitForEngineToken();
			} catch (err) {
				tokenError = err;
			}
		}
		if (activePoll !== poll) return;

		// The same engine must confirm both health and token acceptance.
		timeout = setTimeout(() => poll.abort(), HEALTH_TIMEOUT_MS);
		const resp = await engineFetch('/health', {
			headers: token ? { Authorization: `Bearer ${token}` } : undefined,
			signal: poll.signal
		});
		if (activePoll !== poll) return;
		if (!resp.ok) {
			recordTokenRejection(false);
			healthError.set(true);
			return;
		}
		const data: HealthResponse = await resp.json();
		if (activePoll !== poll) return;
		if (poll.signal.aborted) throw poll.signal.reason;
		health.set(data);
		healthError.set(false);

		const healthy = data.engine === 'healthy' && data.sqlite === 'healthy';
		recordTokenRejection(startupMode && healthy && Boolean(token) && data.token === 'rejected');
		if (startupMode && healthy) {
			if (!token) {
				if (!tokenErrorLogged) {
					tokenErrorLogged = true;
					console.error('Engine is healthy but no engine API token is available', tokenError);
				}
				engineAuthError.set(ENGINE_AUTH_ERROR_MESSAGE);
			} else if (data.token === 'accepted') {
				engineAuthError.set(null);
				startupReady.set(true);
				startupMode = false;
			}
		}

		const settling = startupMode || vectorStoreState(data) === 'starting';
		setPollRate(settling ? FAST_POLL_MS : SLOW_POLL_MS);
	} catch {
		if (activePoll !== poll) return;
		recordTokenRejection(false);
		health.set(null);
		healthError.set(true);
	} finally {
		clearTimeout(timeout);
		if (activePoll === poll) activePoll = null;
	}
}

export function startHealthPolling() {
	stopHealthPolling();
	startupMode = true;
	recordTokenRejection(false);
	pollMs = FAST_POLL_MS;
	pollInterval = setInterval(fetchHealth, pollMs);
	fetchHealth();
}

export function stopHealthPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
	activePoll?.abort();
	activePoll = null;
}
