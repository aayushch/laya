import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const initial: boolean = browser
	? (localStorage.getItem('laya-card-descriptions') ?? 'true') === 'true'
	: true;

const { subscribe, set } = writable<boolean>(initial);

export const cardDescriptions = {
	subscribe,
	set(value: boolean) {
		set(value);
		if (browser) localStorage.setItem('laya-card-descriptions', String(value));
	}
};
