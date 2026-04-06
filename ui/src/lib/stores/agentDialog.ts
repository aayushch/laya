import { writable } from 'svelte/store';

interface AgentDialogState {
	isOpen: boolean;
}

const initial: AgentDialogState = {
	isOpen: false
};

const { subscribe, set, update } = writable<AgentDialogState>(initial);

export const agentDialog = {
	subscribe,
	open() {
		set({ isOpen: true });
	},
	close() {
		update((s) => ({ ...s, isOpen: false }));
	}
};
