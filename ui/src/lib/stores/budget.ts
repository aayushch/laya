// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable, derived } from 'svelte/store';
import { engineApi } from '$lib/api/engine';
import type { BudgetConfig, AgentBudgetStatus } from '$lib/api/types';

export const budgetPaused = writable(false);

/** Full budget data for footer cost display */
export const budgetData = writable<{
	currentMonthCost: number;
	monthlyLimit: number | null;
	enabled: boolean;
	totalTokens: number;
} | null>(null);

function fmtCost(n: number): string {
	return n < 0.01 && n > 0 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

/** Formatted current cost with token volume: "$4.23 (27M)". The token count is
 *  what makes the meter meaningful on a local-model setup, where every model is
 *  free so the dollar figure stays at $0.00 no matter how much you run. */
export const costAmount = derived(budgetData, ($d) => {
	if (!$d) return null;
	const cost = fmtCost($d.currentMonthCost);
	return $d.totalTokens > 0 ? `${cost} (${fmtTokens($d.totalTokens)})` : cost;
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
let _debounceTimer: ReturnType<typeof setTimeout> | null = null;

/** Debounced budget fetch — collapses rapid-fire calls (e.g. from a burst
 *  of card_created / card_updated WS messages) into a single request. */
export function loadBudgetStatus() {
	if (_debounceTimer) clearTimeout(_debounceTimer);
	_debounceTimer = setTimeout(async () => {
		_debounceTimer = null;
		try {
			const data: BudgetConfig = await engineApi.getBudget();
			budgetPaused.set(data.is_paused);
			budgetData.set({
				currentMonthCost: data.current_month_cost,
				monthlyLimit: data.monthly_limit_usd,
				enabled: data.enabled,
				totalTokens: (data.total_input_tokens ?? 0) + (data.total_output_tokens ?? 0),
			});
			_initialized = true;
		} catch {
			// Engine not ready yet — ignore
		}
	}, 5000);
}

export function handleBudgetWsMessage(msg: { type: string; paused?: boolean }) {
	if (msg.type === 'budget_status' && typeof msg.paused === 'boolean') {
		budgetPaused.set(msg.paused);
		// Refresh full cost data when budget status changes (e.g. pause/resume)
		loadBudgetStatus();
	}
}

// ── Agent inference backend usage limits (window-based) ──────────────────
// Parallel to the $ budget above, for when an installed CLI agent is the inference
// backend: agents bill against usage limits, so the footer shows usage / limit too.

export const agentBudgetPaused = writable(false);
export const agentBudgetData = writable<AgentBudgetStatus | null>(null);

function fmtTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
	if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
	return `${n}`;
}

/** The binding agent — highest % among agents that have a token limit set. */
export const agentUsageTop = derived(agentBudgetData, ($d) => {
	if (!$d || !$d.enabled) return null;
	const withLimit = ($d.agents || []).filter((a) => a.window_token_limit > 0);
	if (!withLimit.length) return null;
	return withLimit.reduce((a, b) => ((b.percent ?? 0) > (a.percent ?? 0) ? b : a));
});

/** Formatted "1.2M / 5.0M" — links to settings cost control (null when nothing to show). */
export const agentUsageLabel = derived(agentUsageTop, ($t) =>
	$t ? `${fmtTokens($t.tokens_used)} / ${fmtTokens($t.window_token_limit)}` : null
);

/** Agent usage ratio 0-1 (null when no agent limit configured). */
export const agentUsageRatio = derived(agentUsageTop, ($t) =>
	$t && $t.window_token_limit > 0 ? Math.min($t.tokens_used / $t.window_token_limit, 1) : null
);

let _agentDebounce: ReturnType<typeof setTimeout> | null = null;

/** Debounced agent-usage fetch (mirrors loadBudgetStatus). */
export function loadAgentBudgetStatus() {
	if (_agentDebounce) clearTimeout(_agentDebounce);
	_agentDebounce = setTimeout(async () => {
		_agentDebounce = null;
		try {
			const data = await engineApi.getAgentBudget();
			agentBudgetData.set(data);
			agentBudgetPaused.set(data.is_paused);
		} catch {
			// Engine not ready yet — ignore
		}
	}, 5000);
}

export function handleAgentBudgetWsMessage(msg: { type: string; paused?: boolean }) {
	if (msg.type === 'agent_budget_status' && typeof msg.paused === 'boolean') {
		agentBudgetPaused.set(msg.paused);
		loadAgentBudgetStatus();
	}
}
