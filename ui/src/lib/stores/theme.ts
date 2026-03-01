import { writable } from 'svelte/store';
import { browser } from '$app/environment';

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
