// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';

export type ComposeActionType = string;

interface ComposeState {
	isOpen: boolean;
	platform: string;
	actionType: ComposeActionType;
	prefill: Record<string, unknown>;
	sourceCardId: string | null;
	sourceEventId: string | null;
	connectionId: string | null;
}

const initial: ComposeState = {
	isOpen: false,
	platform: '',
	actionType: 'compose',
	prefill: {},
	sourceCardId: null,
	sourceEventId: null,
	connectionId: null
};

const { subscribe, set, update } = writable<ComposeState>(initial);

export const compose = {
	subscribe,
	openCompose(
		platform: string,
		actionType: ComposeActionType,
		prefill: Record<string, unknown> = {},
		sourceCardId?: string,
		sourceEventId?: string,
		connectionId?: string | null
	) {
		set({
			isOpen: true,
			platform,
			actionType,
			prefill,
			sourceCardId: sourceCardId ?? null,
			sourceEventId: sourceEventId ?? null,
			connectionId: connectionId ?? null
		});
	},
	closeCompose() {
		update((s) => ({ ...s, isOpen: false }));
	}
};
