import { writable } from 'svelte/store';
import { engineApi } from '$lib/api/engine';

export const budgetPaused = writable(false);

let _initialized = false;

export async function loadBudgetStatus() {
	try {
		const data = await engineApi.getBudget();
		budgetPaused.set(data.is_paused);
		_initialized = true;
	} catch {
		// Engine not ready yet — ignore
	}
}

export function handleBudgetWsMessage(msg: { type: string; paused?: boolean }) {
	if (msg.type === 'budget_status' && typeof msg.paused === 'boolean') {
		budgetPaused.set(msg.paused);
	}
}
