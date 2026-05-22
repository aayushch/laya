// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';
import { lastMessage } from './websocket';

/**
 * Tracks which spaces currently have a resynthesis in progress.
 * Survives SPA navigation (in-memory store), resets on full page reload.
 */
export const resynthesizingSpaces = writable<Set<string>>(new Set());

// Global listener — clears resynthesizing flag when resynthesis completes,
// regardless of which page is active. Without this, navigating away during
// resynthesis and back would show a stale spinner if the omni_updated event
// arrived while on another page and was overwritten by subsequent WS messages.
lastMessage.subscribe((msg) => {
	if (msg?.type === 'omni_updated') {
		const snapshotType = msg.payload?.snapshot_type as string | undefined;
		// Only clear for non-incremental updates (resynthesis completion).
		// Incremental updates from new events should NOT clear the spinner.
		if (snapshotType && snapshotType !== 'incremental') {
			const spaceId = (msg.payload?.space_id as string) ?? 'default';
			clearResynthesizing(spaceId);
		}
	}
});

export function markResynthesizing(spaceId: string) {
	resynthesizingSpaces.update((s) => {
		const next = new Set(s);
		next.add(spaceId);
		return next;
	});
}

export function clearResynthesizing(spaceId: string) {
	resynthesizingSpaces.update((s) => {
		const next = new Set(s);
		next.delete(spaceId);
		return next;
	});
}
