// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import type { HealthResponse, UnavailableModel } from '$lib/api/types';
import {
	bannerText,
	fixHref,
	fixLabel,
	modelProblems,
	problemForStage,
	providerLabel
} from './modelAvailability';

function row(overrides: Partial<UnavailableModel> = {}): UnavailableModel {
	return {
		model: 'gemini/gemini-2.0-flash',
		role: 'router',
		space_id: 'default',
		kind: 'not_found',
		reason: 'This model models/gemini-2.0-flash is no longer available.',
		detected_at: '2026-09-30 10:00:00',
		...overrides
	};
}

function health(unavailable: UnavailableModel[] = [], held_events = 0): HealthResponse {
	return {
		engine: 'healthy',
		sqlite: 'healthy',
		n8n: 'healthy',
		uptime_seconds: 60,
		models: { unavailable, held_events }
	};
}

describe('modelProblems', () => {
	it('groups refusals by model and kind and dedupes stages', () => {
		const problems = modelProblems(
			health([row(), row({ role: 'router', space_id: '' }), row({ role: 'stager' })])
		);
		expect(problems).toHaveLength(1);
		expect(problems[0].stages).toEqual(['router', 'stager']);
		expect(problems[0].global).toBe(true);
	});

	it('maps group_summary to the Router stage', () => {
		expect(modelProblems(health([row({ role: 'group_summary' })]))[0].stages).toEqual(['router']);
	});

	it('returns [] when /health has no models field or it is null', () => {
		expect(modelProblems(null)).toEqual([]);
		expect(modelProblems({ ...health(), models: null })).toEqual([]);
		const { models: _omit, ...older } = health();
		expect(modelProblems(older)).toEqual([]);
	});
});

describe('bannerText', () => {
	it('is null when nothing is refused or held', () => {
		expect(bannerText(health())).toBeNull();
		expect(bannerText(null)).toBeNull();
	});

	it('names the provider, model, stages and waiting events', () => {
		expect(bannerText(health([row()], 12))).toBe(
			'Google no longer serves gemini-2.0-flash (Router) — 12 events are waiting and will be processed once you fix it.'
		);
		expect(bannerText(health([row({ kind: 'auth' })], 1))).toBe(
			'Google rejected the API key for gemini-2.0-flash (Router) — 1 event is waiting and will be processed once you fix it.'
		);
		expect(bannerText(health([row({ role: 'chat' })]))).toBe(
			'Google no longer serves gemini-2.0-flash (Chat) — change it to continue.'
		);
	});

	it('falls back to a generic waiting message when events are held but no refusal is recorded', () => {
		expect(bannerText(health([], 3))).toBe(
			'3 events are waiting on a model that was unavailable — try again.'
		);
	});
});

describe('fixHref and fixLabel', () => {
	it('sends a rejected key to the API Keys section with "Change key"', () => {
		const [p] = modelProblems(health([row({ kind: 'auth' })]));
		expect(fixHref(p)).toBe('/settings?tab=models&section=api-keys');
		expect(fixLabel(p)).toBe('Change key');
	});

	it('sends a refused model to its stage dropdown with "Change model"', () => {
		const [p] = modelProblems(health([row({ role: 'stager' })]));
		expect(fixHref(p)).toBe('/settings?tab=models&section=stager-model');
		expect(fixLabel(p)).toBe('Change model');
	});

	it('sends a custom-space problem to the Spaces tab', () => {
		const [p] = modelProblems(health([row({ space_id: 'work' })]));
		expect(fixHref(p)).toBe('/settings?tab=spaces');
	});
});

describe('problemForStage', () => {
	it('ignores refusals that only happened in custom spaces', () => {
		expect(problemForStage(health([row({ space_id: 'work' })]), 'router')).toBeUndefined();
		expect(problemForStage(health([row()]), 'router')?.model).toBe('gemini/gemini-2.0-flash');
	});
});

describe('providerLabel', () => {
	it('names known providers and falls back to the id prefix', () => {
		expect(providerLabel('anthropic/claude-haiku-4-5')).toBe('Anthropic');
		expect(providerLabel('lmstudio-local/qwen')).toBe('lmstudio-local');
	});
});
