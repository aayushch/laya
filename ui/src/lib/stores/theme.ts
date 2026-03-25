import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import { lastMessage } from './websocket';

export type Theme = 'dark' | 'light';

const initial: Theme = browser
	? ((localStorage.getItem('laya-theme') as Theme) ?? 'dark')
	: 'dark';

const { subscribe, set } = writable<Theme>(initial);

export const theme = {
	subscribe,
	set(value: Theme) {
		set(value);
		if (browser) localStorage.setItem('laya-theme', value);
	}
};

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
