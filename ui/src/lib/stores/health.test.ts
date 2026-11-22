// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/config', () => ({
	getEngineUrl: () => 'http://engine.test',
	waitForEngineToken: async () => 'this-launch-token'
}));

const POLL_MS = 1500;

/** Whether the engine answering /health holds this page's token. */
let engineHoldsPageToken = false;

function healthResponse(init?: RequestInit): Response {
	const auth = new Headers(init?.headers).get('Authorization');
	const token =
		auth === null
			? 'absent'
			: engineHoldsPageToken && auth === 'Bearer this-launch-token'
				? 'accepted'
				: 'rejected';
	const body = { engine: 'healthy', sqlite: 'healthy', n8n: 'healthy', uptime_seconds: 5, token };
	return new Response(JSON.stringify(body), { status: 200 });
}

async function loadHealthStore() {
	vi.resetModules();
	return import('./health');
}

describe('startup readiness', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		engineHoldsPageToken = false;
		vi.stubGlobal(
			'fetch',
			vi.fn(async (_url: string, init?: RequestInit) => healthResponse(init))
		);
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('waits for the engine that accepts this page token', async () => {
		const store = await loadHealthStore();
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(POLL_MS * 3);

		// A healthy engine from the previous launch answers, but refuses the token.
		expect(get(store.startupReady)).toBe(false);
		expect(get(store.engineAuthError)).toBeNull();

		// This launch's engine takes over the port.
		engineHoldsPageToken = true;
		await vi.advanceTimersByTimeAsync(POLL_MS);
		expect(get(store.startupReady)).toBe(true);
		store.stopHealthPolling();
	});

	it('reports an engine that keeps refusing the token', async () => {
		const store = await loadHealthStore();
		store.startHealthPolling();
		// The first check runs at once, then one per poll.
		await vi.advanceTimersByTimeAsync(POLL_MS * (store.TOKEN_REJECTED_CHECKS_BEFORE_ERROR - 2));
		expect(get(store.engineAuthError)).toBeNull();

		await vi.advanceTimersByTimeAsync(POLL_MS);
		expect(get(store.engineAuthError)).toContain('does not accept');
		expect(get(store.startupReady)).toBe(false);
		store.stopHealthPolling();
	});
});
