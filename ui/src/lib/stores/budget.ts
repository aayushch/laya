import { writable, derived } from 'svelte/store';
import { engineApi } from '$lib/api/engine';
import type { BudgetConfig } from '$lib/api/types';

export const budgetPaused = writable(false);

/** Full budget data for footer cost display */
export const budgetData = writable<{
	currentMonthCost: number;
	monthlyLimit: number | null;
	enabled: boolean;
} | null>(null);

function fmtCost(n: number): string {
	return n < 0.01 && n > 0 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

/** Formatted current cost: "$4.23" — links to /status */
export const costAmount = derived(budgetData, ($d) => {
	if (!$d) return null;
	return fmtCost($d.currentMonthCost);
});

/** Formatted budget limit: "/ $30.00" — links to settings cost control (null if no budget) */
export const budgetLabel = derived(budgetData, ($d) => {
	if (!$d || !$d.enabled || $d.monthlyLimit == null) return null;
	return `/ ${fmtCost($d.monthlyLimit)}`;
});

/** Combined label for accessibility / tooltip */
export const costLabel = derived([costAmount, budgetLabel], ([$cost, $budget]) => {
	if (!$cost) return null;
	return $budget ? `${$cost} ${$budget}` : $cost;
});

/** Budget usage ratio 0-1 (null if budget not enabled) */
export const budgetRatio = derived(budgetData, ($d) => {
	if (!$d || !$d.enabled || $d.monthlyLimit == null || $d.monthlyLimit <= 0) return null;
	return Math.min($d.currentMonthCost / $d.monthlyLimit, 1);
});

let _initialized = false;

export async function loadBudgetStatus() {
	try {
		const data: BudgetConfig = await engineApi.getBudget();
		budgetPaused.set(data.is_paused);
		budgetData.set({
			currentMonthCost: data.current_month_cost,
			monthlyLimit: data.monthly_limit_usd,
			enabled: data.enabled,
		});
		_initialized = true;
	} catch {
		// Engine not ready yet — ignore
	}
}

export function handleBudgetWsMessage(msg: { type: string; paused?: boolean }) {
	if (msg.type === 'budget_status' && typeof msg.paused === 'boolean') {
		budgetPaused.set(msg.paused);
		// Refresh full cost data when budget status changes (e.g. pause/resume)
		loadBudgetStatus();
	}
}
