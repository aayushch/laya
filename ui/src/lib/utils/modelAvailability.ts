// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import type { HealthResponse, ModelRefusalKind } from '$lib/api/types';

/** Settings stage each engine role takes its model from (group_summary → router). */
export const ROLE_TO_STAGE: Record<string, string> = {
	router: 'router',
	stager: 'stager',
	chat: 'chat',
	trace: 'trace',
	omni: 'omni',
	group_summary: 'router'
};

/** Stage names as shown in Settings → Models. */
export const STAGE_LABELS: Record<string, string> = {
	router: 'Router',
	stager: 'Stager',
	chat: 'Chat',
	trace: 'Coherence',
	omni: 'Omni'
};

const PROVIDER_LABELS: Record<string, string> = {
	gemini: 'Google',
	anthropic: 'Anthropic',
	openai: 'OpenAI',
	openrouter: 'OpenRouter'
};

/** A refused model, merged across the roles and spaces that hit it. */
export interface ModelProblem {
	model: string;
	kind: ModelRefusalKind;
	/** Settings stage ids, deduped. */
	stages: string[];
	/** True when the global setting, not a custom space, is at fault. */
	global: boolean;
	reason: string;
}

/** Provider name for a model id, e.g. `gemini/…` → "Google". */
export function providerLabel(model: string): string {
	const prefix = model.includes('/') ? model.split('/')[0] : '';
	return PROVIDER_LABELS[prefix] ?? (prefix || 'The provider');
}

/** Model id without a known provider prefix, e.g. `gemini/gemini-2.0-flash` → `gemini-2.0-flash`. */
function modelName(model: string): string {
	const [prefix, ...rest] = model.split('/');
	return PROVIDER_LABELS[prefix] && rest.length ? rest.join('/') : model;
}

/** Refusals from /health grouped by model and kind; [] when none are reported. */
export function modelProblems(health: HealthResponse | null): ModelProblem[] {
	const problems: ModelProblem[] = [];
	for (const row of health?.models?.unavailable ?? []) {
		const stage = ROLE_TO_STAGE[row.role] ?? row.role;
		const global = row.space_id === '' || row.space_id === 'default';
		const existing = problems.find((p) => p.model === row.model && p.kind === row.kind);
		if (existing) {
			if (!existing.stages.includes(stage)) existing.stages.push(stage);
			existing.global ||= global;
		} else {
			problems.push({ model: row.model, kind: row.kind, stages: [stage], global, reason: row.reason });
		}
	}
	return problems;
}

/** The global-scope problem for a settings stage, if any. */
export function problemForStage(
	health: HealthResponse | null,
	stage: string
): ModelProblem | undefined {
	return modelProblems(health).find((p) => p.global && p.stages.includes(stage));
}

function eventsWaiting(n: number): string {
	return n === 1 ? '1 event is waiting' : `${n} events are waiting`;
}

/** Banner sentence, or null when nothing is refused or held. */
export function bannerText(health: HealthResponse | null): string | null {
	const problems = modelProblems(health);
	const held = health?.models?.held_events ?? 0;
	if (problems.length === 0) {
		return held > 0 ? `${eventsWaiting(held)} on a model that was unavailable — try again.` : null;
	}
	const p = problems[0];
	const provider = providerLabel(p.model);
	const name = modelName(p.model);
	const refusal = {
		not_found: `${provider} no longer serves ${name}`,
		auth: `${provider} rejected the API key for ${name}`,
		permission: `Your ${provider} key isn't allowed to use ${name}`
	}[p.kind];
	const stages = p.stages.map((s) => STAGE_LABELS[s] ?? s).join(', ');
	const more = problems.length > 1 ? ` and ${problems.length - 1} more` : '';
	const tail =
		held > 0
			? ` — ${eventsWaiting(held)} and will be processed once you fix it.`
			: ' — change it to continue.';
	return `${refusal} (${stages})${more}${tail}`;
}

/** "Change key" for a rejected key, otherwise "Change model". */
export function fixLabel(problem: ModelProblem): string {
	return problem.kind === 'auth' ? 'Change key' : 'Change model';
}

/** Settings link that fixes the problem: Spaces tab, API Keys section or the stage's dropdown. */
export function fixHref(problem: ModelProblem): string {
	if (!problem.global) return '/settings?tab=spaces';
	if (problem.kind === 'auth') return '/settings?tab=models&section=api-keys';
	return `/settings?tab=models&section=${problem.stages[0]}-model`;
}
