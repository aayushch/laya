import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const initial: boolean = browser
	? (localStorage.getItem('laya-card-descriptions') ?? 'true') === 'true'
	: true;

const { subscribe, set, update } = writable<boolean>(initial);

function persist(value: boolean) {
	if (browser) localStorage.setItem('laya-card-descriptions', String(value));
}

export const cardDescriptions = {
	subscribe,
	set(value: boolean) {
		set(value);
		persist(value);
	},
	toggle() {
		update((v) => {
			const next = !v;
			persist(next);
			return next;
		});
	}
};
