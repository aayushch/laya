// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/config', () => ({
	getEngineUrl: () => 'http://engine.test',
	getEngineToken: () => pageHasToken ? 'this-launch-token' : '',
	waitForEngineToken: async () => {
		if (!pageHasToken) throw new Error('token unavailable');
		return 'this-launch-token';
	}
}));

const POLL_MS = 1500;

/** Whether the engine answering /health holds this page's token. */
let engineHoldsPageToken = false;
let pageHasToken = true;

function healthResponse(init?: RequestInit, sqlite = 'healthy'): Response {
	const auth = new Headers(init?.headers).get('Authorization');
	const token =
		auth === null
			? 'absent'
			: engineHoldsPageToken && auth === 'Bearer this-launch-token'
				? 'accepted'
				: 'rejected';
	const body = { engine: 'healthy', sqlite, n8n: 'healthy', uptime_seconds: 5, token };
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
		pageHasToken = true;
		vi.stubGlobal(
			'fetch',
			vi.fn(async (_url: string, init?: RequestInit) => healthResponse(init))
		);
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
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

	it('requires SQLite health and token acceptance on the same response', async () => {
		engineHoldsPageToken = true;
		const fetchHealth = vi.fn(async (_url: string, init?: RequestInit) => {
			const authenticated = new Headers(init?.headers).has('Authorization');
			return healthResponse(init, authenticated ? 'unhealthy' : 'healthy');
		});
		vi.stubGlobal('fetch', fetchHealth);

		const store = await loadHealthStore();
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(0);

		expect(get(store.startupReady)).toBe(false);
		expect(get(store.health)?.sqlite).toBe('unhealthy');
		expect(fetchHealth).toHaveBeenCalledTimes(1);
		expect(new Headers(fetchHealth.mock.calls[0][1]?.headers).get('Authorization')).toBe(
			'Bearer this-launch-token'
		);
		store.stopHealthPolling();
	});

	it('retries when an authenticated health request stalls', async () => {
		engineHoldsPageToken = true;
		let firstAuthenticatedRequest = true;
		const fetchHealth = vi.fn(async (_url: string, init?: RequestInit) => {
			if (firstAuthenticatedRequest && new Headers(init?.headers).has('Authorization')) {
				firstAuthenticatedRequest = false;
				return new Promise<Response>((_, reject) => {
					const signal = init?.signal;
					signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
				});
			}
			return healthResponse(init);
		});
		vi.stubGlobal('fetch', fetchHealth);

		const store = await loadHealthStore();
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(POLL_MS);
		expect(get(store.startupReady)).toBe(false);
		expect(fetchHealth).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(4 * POLL_MS);
		expect(get(store.startupReady)).toBe(true);
		store.stopHealthPolling();
	});

	it('ignores a response from polling that was stopped', async () => {
		engineHoldsPageToken = true;
		const oldResponse = healthResponse({ headers: { Authorization: 'Bearer this-launch-token' } });
		let resolveFirst!: (response: Response) => void;
		const firstRequest = new Promise<Response>((resolve) => { resolveFirst = resolve; });
		const fetchHealth = vi.fn(async (_url: string, init?: RequestInit) => healthResponse(init));
		fetchHealth.mockReturnValueOnce(firstRequest);
		vi.stubGlobal('fetch', fetchHealth);

		const store = await loadHealthStore();
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(0);
		store.stopHealthPolling();
		expect(fetchHealth.mock.calls[0][1]?.signal?.aborted).toBe(true);

		engineHoldsPageToken = false;
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(0);
		resolveFirst(oldResponse);
		await vi.advanceTimersByTimeAsync(0);

		expect(get(store.startupReady)).toBe(false);
		expect(get(store.health)?.token).toBe('rejected');
		store.stopHealthPolling();
	});

	it('reports a missing token when the engine is healthy and clears it after recovery', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		pageHasToken = false;
		engineHoldsPageToken = true;

		const store = await loadHealthStore();
		store.startHealthPolling();
		await vi.advanceTimersByTimeAsync(0);
		expect(get(store.health)?.engine).toBe('healthy');
		expect(get(store.engineAuthError)).toContain('could not get');
		expect(get(store.startupReady)).toBe(false);

		pageHasToken = true;
		await vi.advanceTimersByTimeAsync(POLL_MS);
		expect(get(store.engineAuthError)).toBeNull();
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
