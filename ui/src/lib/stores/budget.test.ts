// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { AgentBudgetStatus } from '$lib/api/types';
import {
	fmtTokens,
	budgetData,
	costAmount,
	budgetLabel,
	costLabel,
	budgetRatio,
	agentBudgetData,
	agentUsageTop,
	agentUsageLabel,
	agentUsageRatio
} from './budget';

// The derived stores recompute from their source writable on every read, so
// resetting the sources to null between tests keeps cases independent.
beforeEach(() => {
	budgetData.set(null);
	agentBudgetData.set(null);
});

describe('fmtTokens', () => {
	it('formats millions (one decimal below 10M, whole number at/above)', () => {
		expect(fmtTokens(1_500_000)).toBe('1.5M');
		expect(fmtTokens(27_000_000)).toBe('27M');
		expect(fmtTokens(10_000_000)).toBe('10M');
	});
	it('formats thousands with K, rounded', () => {
		expect(fmtTokens(27_000)).toBe('27K');
		expect(fmtTokens(1_500)).toBe('2K'); // Math.round(1.5) → 2
	});
	it('leaves sub-thousand counts as-is', () => {
		expect(fmtTokens(999)).toBe('999');
		expect(fmtTokens(0)).toBe('0');
	});
});

describe('$ budget derived stores', () => {
	it('costAmount is null with no data', () => {
		expect(get(costAmount)).toBeNull();
	});

	it('costAmount appends the token volume only when tokens > 0', () => {
		budgetData.set({ currentMonthCost: 4.23, monthlyLimit: 30, enabled: true, totalTokens: 27_000_000 });
		expect(get(costAmount)).toBe('$4.23 (27M)');
		budgetData.set({ currentMonthCost: 4.23, monthlyLimit: 30, enabled: true, totalTokens: 0 });
		expect(get(costAmount)).toBe('$4.23');
	});

	it('costAmount uses 4-decimal precision for tiny non-zero costs', () => {
		budgetData.set({ currentMonthCost: 0.005, monthlyLimit: null, enabled: false, totalTokens: 0 });
		expect(get(costAmount)).toBe('$0.0050');
		budgetData.set({ currentMonthCost: 0, monthlyLimit: null, enabled: false, totalTokens: 0 });
		expect(get(costAmount)).toBe('$0.00');
	});

	it('budgetLabel shows the limit only when a budget is enabled', () => {
		budgetData.set({ currentMonthCost: 4, monthlyLimit: 30, enabled: true, totalTokens: 0 });
		expect(get(budgetLabel)).toBe('/ $30.00');
		budgetData.set({ currentMonthCost: 4, monthlyLimit: 30, enabled: false, totalTokens: 0 });
		expect(get(budgetLabel)).toBeNull();
		budgetData.set({ currentMonthCost: 4, monthlyLimit: null, enabled: true, totalTokens: 0 });
		expect(get(budgetLabel)).toBeNull();
	});

	it('costLabel joins the cost and the limit when both exist', () => {
		budgetData.set({ currentMonthCost: 4.23, monthlyLimit: 30, enabled: true, totalTokens: 0 });
		expect(get(costLabel)).toBe('$4.23 / $30.00');
	});

	it('budgetRatio clamps to [0,1] and is null without an enabled positive limit', () => {
		budgetData.set({ currentMonthCost: 15, monthlyLimit: 30, enabled: true, totalTokens: 0 });
		expect(get(budgetRatio)).toBe(0.5);
		budgetData.set({ currentMonthCost: 45, monthlyLimit: 30, enabled: true, totalTokens: 0 });
		expect(get(budgetRatio)).toBe(1); // over budget → clamped
		budgetData.set({ currentMonthCost: 5, monthlyLimit: 0, enabled: true, totalTokens: 0 });
		expect(get(budgetRatio)).toBeNull();
	});
});

describe('agent usage derived stores', () => {
	function status(agents: Array<Partial<AgentBudgetStatus['agents'][number]>>, enabled = true): AgentBudgetStatus {
		return { enabled, agents, is_paused: false, paused_until: null, paused_reason: null, paused_workflow_count: 0 } as AgentBudgetStatus;
	}

	it('agentUsageTop is null when disabled or no agent has a limit', () => {
		expect(get(agentUsageTop)).toBeNull(); // null data
		agentBudgetData.set(status([{ window_token_limit: 100, percent: 10 }], false));
		expect(get(agentUsageTop)).toBeNull(); // disabled
		agentBudgetData.set(status([{ window_token_limit: 0, percent: 90 }]));
		expect(get(agentUsageTop)).toBeNull(); // no positive limit
	});

	it('agentUsageTop picks the agent with the highest percent', () => {
		agentBudgetData.set(
			status([
				{ agent_id: 'a', window_token_limit: 100, tokens_used: 20, percent: 20 },
				{ agent_id: 'b', window_token_limit: 100, tokens_used: 80, percent: 80 }
			])
		);
		expect(get(agentUsageTop)?.agent_id).toBe('b');
	});

	it('agentUsageLabel and agentUsageRatio format the binding agent', () => {
		agentBudgetData.set(
			status([{ agent_id: 'a', window_token_limit: 5_000_000, tokens_used: 1_200_000, percent: 24 }])
		);
		expect(get(agentUsageLabel)).toBe('1.2M / 5.0M');
		expect(get(agentUsageRatio)).toBeCloseTo(0.24);
	});

	it('agentUsageRatio clamps to 1 when usage exceeds the limit', () => {
		agentBudgetData.set(
			status([{ agent_id: 'a', window_token_limit: 1_000_000, tokens_used: 3_000_000, percent: 300 }])
		);
		expect(get(agentUsageRatio)).toBe(1);
	});
});
