// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest';
import { createReconnectReload } from './reconnectReload';

describe('feed reconnect reload', () => {
	it.each(['before loading', 'during loading'])('retries once when the socket connects %s', async (when) => {
		const reload = vi.fn();
		const feed = createReconnectReload(reload);
		if (when === 'before loading') feed.connectionChanged(true);

		let failRequest!: (reason: Error) => void;
		const request = new Promise<void>((_, reject) => {
			failRequest = reject;
		});
		const load = request.catch(() => feed.loadFinished(false));
		if (when === 'during loading') feed.connectionChanged(true);
		expect(reload).not.toHaveBeenCalled();

		failRequest(new Error('engine request failed'));
		await load;
		expect(reload).toHaveBeenCalledTimes(1);

		feed.loadFinished(false);
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('retries a failed initial load when the first connection arrives', () => {
		const reload = vi.fn();
		const feed = createReconnectReload(reload);
		feed.loadFinished(false);
		expect(reload).not.toHaveBeenCalled();

		feed.connectionChanged(true);
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('reloads on reconnect after a successful initial load', () => {
		const reload = vi.fn();
		const feed = createReconnectReload(reload);
		feed.connectionChanged(true);
		feed.loadFinished(true);
		feed.loadFinished(false);
		expect(reload).not.toHaveBeenCalled();

		feed.connectionChanged(false);
		feed.connectionChanged(true);
		feed.connectionChanged(true);
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('avoids a second retry when reconnect already scheduled a reload', () => {
		const reload = vi.fn();
		const feed = createReconnectReload(reload);
		feed.connectionChanged(true);
		feed.connectionChanged(false);
		feed.connectionChanged(true);
		feed.loadFinished(false);
		expect(reload).toHaveBeenCalledTimes(1);
	});
});
