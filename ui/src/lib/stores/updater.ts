import { writable } from 'svelte/store';
import { browser } from '$app/environment';

interface UpdateState {
	available: boolean;
	version: string | null;
	body: string | null;
	downloading: boolean;
	progress: number;
	ready: boolean;
	error: string | null;
}

const initial: UpdateState = {
	available: false,
	version: null,
	body: null,
	downloading: false,
	progress: 0,
	ready: false,
	error: null
};

export const updateState = writable<UpdateState>(initial);

let updateObj: any = null;
let contentLength = 0;
let downloaded = 0;

export async function checkForUpdate() {
	if (!browser) return;
	try {
		const { check } = await import('@tauri-apps/plugin-updater');
		const update = await check();
		if (update) {
			updateObj = update;
			updateState.set({
				...initial,
				available: true,
				version: update.version,
				body: update.body ?? null
			});
		}
	} catch (e) {
		console.error('Update check failed:', e);
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
