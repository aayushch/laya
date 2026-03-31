import { writable } from 'svelte/store';

export type ComposeActionType = 'reply' | 'compose' | 'comment' | 'forward';

interface ComposeState {
	isOpen: boolean;
	platform: string;
	actionType: ComposeActionType;
	prefill: Record<string, unknown>;
	sourceCardId: string | null;
}

const initial: ComposeState = {
	isOpen: false,
	platform: '',
	actionType: 'compose',
	prefill: {},
	sourceCardId: null
};

const { subscribe, set, update } = writable<ComposeState>(initial);

export const compose = {
	subscribe,
	openCompose(
		platform: string,
		actionType: ComposeActionType,
		prefill: Record<string, unknown> = {},
		sourceCardId?: string
	) {
		set({
			isOpen: true,
			platform,
			actionType,
			prefill,
			sourceCardId: sourceCardId ?? null
		});
	},
	closeCompose() {
		update((s) => ({ ...s, isOpen: false }));
	}
};
