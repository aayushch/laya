import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import { lastMessage } from './websocket';

export type Theme = 'dark' | 'light';

const initial: Theme = browser
	? ((localStorage.getItem('laya-theme') as Theme) ?? 'dark')
	: 'dark';

const { subscribe, set } = writable<Theme>(initial);

/** Sync native window background with theme so macOS traffic lights
 *  stay visible when the window loses focus (grayscale lights blend
 *  into a mismatched background). */
async function syncNativeTheme(value: Theme) {
	try {
		const { invoke } = await import('@tauri-apps/api/core');
		await invoke('set_window_theme', { theme: value });
	} catch {
		// Not running inside Tauri shell (e.g. dev server in browser)
	}
}

export const theme = {
	subscribe,
	set(value: Theme) {
		set(value);
		if (browser) {
			localStorage.setItem('laya-theme', value);
			syncNativeTheme(value);
		}
	}
};

// Apply initial theme to native window on startup
if (browser) syncNativeTheme(initial);

// React to settings_changed events broadcast by update_theme tool
if (browser) {
	lastMessage.subscribe((msg) => {
		if (msg?.type === 'settings_changed' && msg.payload?.section === 'appearance') {
			const newTheme = (msg.payload.new_value as Record<string, unknown>)?.theme as Theme;
			if (newTheme === 'dark' || newTheme === 'light') {
				theme.set(newTheme);
			}
		}
	});
}
