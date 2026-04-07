import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type FontScale = 12 | 13 | 14 | 15;

const VALID: FontScale[] = [12, 13, 14, 15];

function readInitial(): FontScale {
	if (!browser) return 13;
	const raw = localStorage.getItem('laya-font-scale');
	const parsed = Number(raw) as FontScale;
	return VALID.includes(parsed) ? parsed : 13;
}

const { subscribe, set } = writable<FontScale>(readInitial());

export const fontScale = {
	subscribe,
	set(value: FontScale) {
		set(value);
		if (browser) localStorage.setItem('laya-font-scale', String(value));
	}
};
