// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';

interface UpdateState {
	available: boolean;
	version: string | null;
	body: string | null;
	downloading: boolean;
	progress: number;
	ready: boolean;
	error: string | null;
	checking: boolean;
	lastCheckedAt: number | null;
}

const initial: UpdateState = {
	available: false,
	version: null,
	body: null,
	downloading: false,
	progress: 0,
	ready: false,
	error: null,
	checking: false,
	lastCheckedAt: null
};

export const updateState = writable<UpdateState>(initial);

let updateObj: any = null;
let contentLength = 0;
let downloaded = 0;
let periodicTimer: ReturnType<typeof setInterval> | null = null;
let startupTimer: ReturnType<typeof setTimeout> | null = null;

export type CheckResult = 'available' | 'up-to-date' | 'error';

export async function checkForUpdate(): Promise<CheckResult> {
	if (!browser) return 'error';
	// Don't re-check while a download is in flight or an install is pending.
	const current = get(updateState);
	if (current.downloading || current.ready) return 'available';

	updateState.update((s) => ({ ...s, checking: true, error: null }));
	try {
		const { check } = await import('@tauri-apps/plugin-updater');
		const update = await check();
		const now = Date.now();
		if (update) {
			updateObj = update;
			updateState.set({
				...initial,
				available: true,
				version: update.version,
				body: update.body ?? null,
				lastCheckedAt: now
			});
			return 'available';
		}
		updateState.update((s) => ({ ...s, checking: false, lastCheckedAt: now }));
		return 'up-to-date';
	} catch (e: any) {
		console.error('Update check failed:', e);
		updateState.update((s) => ({
			...s,
			checking: false,
			error: e?.message ?? String(e),
			lastCheckedAt: Date.now()
		}));
		return 'error';
	}
}

// Two-hour periodic check is a common cadence for desktop apps (VS Code, Slack
// are in this range). Long enough not to hammer the release endpoint, short
// enough that users on always-open sessions notice releases within the day.
const PERIODIC_INTERVAL_MS = 2 * 60 * 60 * 1000;

export function startPeriodicCheck(initialDelayMs = 5000) {
	if (!browser) return;
	stopPeriodicCheck();
	startupTimer = setTimeout(() => {
		void checkForUpdate();
		periodicTimer = setInterval(() => {
			void checkForUpdate();
		}, PERIODIC_INTERVAL_MS);
	}, initialDelayMs);
}

export function stopPeriodicCheck() {
	if (startupTimer) {
		clearTimeout(startupTimer);
		startupTimer = null;
	}
	if (periodicTimer) {
		clearInterval(periodicTimer);
		periodicTimer = null;
	}
}

export async function downloadAndInstall() {
	if (!updateObj) return;
	contentLength = 0;
	downloaded = 0;
	updateState.update((s) => ({ ...s, downloading: true, error: null, progress: 0 }));
	try {
		await updateObj.downloadAndInstall((event: any) => {
			if (event.event === 'Started') {
				contentLength = event.data.contentLength ?? 0;
			} else if (event.event === 'Progress') {
				downloaded += event.data.chunkLength ?? 0;
				const pct = contentLength > 0 ? Math.round((downloaded / contentLength) * 100) : 0;
				updateState.update((s) => ({ ...s, progress: pct }));
			} else if (event.event === 'Finished') {
				updateState.update((s) => ({ ...s, downloading: false, ready: true, progress: 100 }));
			}
		});
	} catch (e: any) {
		updateState.update((s) => ({
			...s,
			downloading: false,
			error: e?.message ?? String(e)
		}));
	}
}

export async function installAndRelaunch() {
	try {
		const { relaunch } = await import('@tauri-apps/plugin-process');
		await relaunch();
	} catch (e) {
		console.error('Relaunch failed:', e);
	}
}
