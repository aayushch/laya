<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import ProcessingRulesEditor from './ProcessingRulesEditor.svelte';
	import Dropdown from '$lib/components/Dropdown.svelte';
	import type { Rule, RuleCondition, SimpleCondition, ClassificationRule } from '$lib/api/types';

	const operators: SimpleCondition['operator'][] = ['equals', 'not_equals', 'contains', 'starts_with', 'ends_with', 'in'];
	const commonFields = [
		'actor.email',
		'actor.name',
		'actor.platform_handle',
		'source.platform',
		'source.connection_id',
		'source.raw_event_type',
		'subject.type',
		'subject.id',
		'subject.title',
		'subject.url',
		'content.body',
		'content.metadata.slack_channel',
		'content.metadata.jira_project'
	];

	interface ConditionRow {
		field: string;
		operator: SimpleCondition['operator'];
		value: string;
	}

	// ── Filter rules state ───────────────────────────────────────────────
	let rules = $state<Rule[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let editingIndex = $state<number | null>(null);
	let showAddForm = $state(false);

	// Form fields (filter rules)
	let formName = $state('');
	let formAction = $state<'drop' | 'allow'>('drop');
	let formLogic = $state<'all' | 'any'>('all');
	let formConditions = $state<ConditionRow[]>([{ field: 'actor.email', operator: 'contains', value: '' }]);

	// ── Classification rules state ───────────────────────────────────────
	let clsRules = $state<ClassificationRule[]>([]);
	let clsLoading = $state(true);
	let clsError = $state<string | null>(null);
	let clsSaving = $state(false);
	let showClsAddForm = $state(false);
	let clsEditingId = $state<number | null>(null);
	let clsFormText = $state('');
	let clsFormField = $state<string | null>(null);

	const clsFieldOptions = [
		{ value: null, label: 'General' },
		{ value: 'priority', label: 'Priority' },
		{ value: 'persona', label: 'Persona' }
	];

	onMount(async () => {
		try {
			const data = await engineApi.getRules();
			rules = data.rules;
		} catch {
			error = 'Failed to load rules configuration';
		} finally {
			loading = false;
		}

		try {
			clsRules = await engineApi.getClassificationRules();
		} catch {
			clsError = 'Failed to load classification rules';
		} finally {
			clsLoading = false;
		}
	});

	// ── Filter rules functions ───────────────────────────────────────────

	async function save() {
		saving = true;
		error = null;
		try {
			await engineApi.updateRules({ rules });
		} catch {
			error = 'Failed to save rules';
		} finally {
			saving = false;
		}
	}

	function conditionSummary(condition: RuleCondition): string {
		if ('field' in condition) {
			return `${condition.field} ${condition.operator.replace('_', ' ')} "${condition.value}"`;
		}
		if ('all' in condition) {
			return condition.all.map(conditionSummary).join(' AND ');
		}
		if ('any' in condition) {
			return condition.any.map(conditionSummary).join(' OR ');
		}
		return 'Unknown condition';
	}

	function conditionLabel(condition: RuleCondition): string | null {
		if ('all' in condition && condition.all.length > 1) return 'AND';
		if ('any' in condition && condition.any.length > 1) return 'OR';
		return null;
	}

	function decomposeCondition(condition: RuleCondition): { logic: 'all' | 'any'; rows: ConditionRow[] } {
		if ('field' in condition) {
			const c = condition as SimpleCondition;
			return {
				logic: 'all',
				rows: [{ field: c.field, operator: c.operator, value: typeof c.value === 'string' ? c.value : c.value.join(', ') }]
			};
		}
		if ('all' in condition) {
			return {
				logic: 'all',
				rows: condition.all.filter((c): c is SimpleCondition => 'field' in c).map(c => ({
					field: c.field, operator: c.operator, value: typeof c.value === 'string' ? c.value : c.value.join(', ')
				}))
			};
		}
		if ('any' in condition) {
			return {
				logic: 'any',
				rows: condition.any.filter((c): c is SimpleCondition => 'field' in c).map(c => ({
					field: c.field, operator: c.operator, value: typeof c.value === 'string' ? c.value : c.value.join(', ')
				}))
			};
		}
		return { logic: 'all', rows: [{ field: 'actor.email', operator: 'contains', value: '' }] };
	}

	function buildCondition(): RuleCondition {
		const simples: SimpleCondition[] = formConditions.map(r => ({
			field: r.field,
			operator: r.operator,
			value: r.value
		}));
		if (simples.length === 1) return simples[0];
		if (formLogic === 'any') return { any: simples };
		return { all: simples };
	}

	function resetForm() {
		formName = '';
		formAction = 'drop';
		formLogic = 'all';
		formConditions = [{ field: 'actor.email', operator: 'contains', value: '' }];
		showAddForm = false;
		editingIndex = null;
	}

	function addConditionRow() {
		formConditions.push({ field: 'actor.email', operator: 'contains', value: '' });
	}

	function removeConditionRow(index: number) {
		if (formConditions.length > 1) {
			formConditions.splice(index, 1);
		}
	}

	let formValid = $derived(formName.trim() !== '' && formConditions.every(c => c.value.trim() !== ''));

	async function addRule() {
		rules.push({
			name: formName,
			enabled: true,
			condition: buildCondition(),
			action: formAction
		});
		resetForm();
		await save();
	}

	function startEdit(index: number) {
		const r = rules[index];
		formName = r.name;
		formAction = r.action;
		const { logic, rows } = decomposeCondition(r.condition);
		formLogic = logic;
		formConditions = rows.length ? rows : [{ field: 'actor.email', operator: 'contains', value: '' }];
		editingIndex = index;
		showAddForm = false;
	}

	async function saveEdit() {
		if (editingIndex !== null) {
			rules[editingIndex] = {
				...rules[editingIndex],
				name: formName,
				action: formAction,
				condition: buildCondition()
			};
			resetForm();
			await save();
		}
	}

	async function toggleRule(index: number) {
		rules[index].enabled = !rules[index].enabled;
		await save();
	}

	async function removeRule(index: number) {
		rules.splice(index, 1);
		await save();
	}

	// ── Classification rules functions ───────────────────────────────────

	function resetClsForm() {
		clsFormText = '';
		clsFormField = null;
		showClsAddForm = false;
		clsEditingId = null;
	}

	async function addClsRule() {
		clsSaving = true;
		clsError = null;
		try {
			const result = await engineApi.createClassificationRule({
				rule_text: clsFormText,
				field: clsFormField
			});
			clsRules = [
				{ id: result.id, space_id: null, field: clsFormField, rule_text: clsFormText, source: 'manual', active: true, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
				...clsRules
			];
			resetClsForm();
		} catch {
			clsError = 'Failed to create rule';
		} finally {
			clsSaving = false;
		}
	}

	function startClsEdit(rule: ClassificationRule) {
		clsEditingId = rule.id;
		clsFormText = rule.rule_text;
		clsFormField = rule.field;
		showClsAddForm = false;
	}

	async function saveClsEdit() {
		if (clsEditingId === null) return;
		clsSaving = true;
		clsError = null;
		try {
			await engineApi.updateClassificationRule(clsEditingId, {
				rule_text: clsFormText,
				field: clsFormField
			});
			const idx = clsRules.findIndex(r => r.id === clsEditingId);
			if (idx !== -1) {
				clsRules[idx] = { ...clsRules[idx], rule_text: clsFormText, field: clsFormField };
			}
			resetClsForm();
		} catch {
			clsError = 'Failed to update rule';
		} finally {
			clsSaving = false;
		}
	}

	async function toggleClsRule(rule: ClassificationRule) {
		try {
			await engineApi.updateClassificationRule(rule.id, { active: !rule.active });
			const idx = clsRules.findIndex(r => r.id === rule.id);
			if (idx !== -1) clsRules[idx] = { ...clsRules[idx], active: !rule.active };
		} catch {
			clsError = 'Failed to toggle rule';
		}
	}

	async function removeClsRule(rule: ClassificationRule) {
		try {
			await engineApi.deleteClassificationRule(rule.id);
			clsRules = clsRules.filter(r => r.id !== rule.id);
		} catch {
			clsError = 'Failed to delete rule';
		}
	}

	let clsFormValid = $derived(clsFormText.trim() !== '');
</script>

{#if loading && clsLoading}
	<div class="text-surface-400">Loading rules...</div>
{:else}
	<!-- ═══════════════ FILTER RULES ═══════════════ -->
	<div class="space-y-4">
		<div>
			<h3 class="text-laya-heading font-semibold text-surface-50">Filter Rules</h3>
			<p class="mt-1 text-laya-base text-surface-400">
				Filter rules control which events Laya processes. Use these to ignore noisy notifications or allow only specific sources.
			</p>
		</div>

		{#if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-laya-base text-red-300">{error}</div>
		{/if}

		{#if loading}
			<div class="text-surface-400 text-laya-base">Loading filter rules...</div>
		{:else}
			<div class="space-y-3">
				{#each rules as rule, i}
					<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4">
						<div class="flex items-start justify-between">
							<div class="flex-1">
								<div class="flex items-center gap-3">
									<button
										class="relative h-5 w-9 rounded-full transition-colors {rule.enabled ? 'bg-green-600' : 'bg-surface-600'}"
										onclick={() => toggleRule(i)}
										aria-label="Toggle rule"
									>
										<span class="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform {rule.enabled ? 'left-[1.125rem]' : 'left-0.5'}"></span>
									</button>
									<span class="rounded px-1.5 py-0.5 text-laya-secondary font-semibold uppercase {rule.action === 'allow' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'}">{rule.action === 'allow' ? 'Allow' : 'Ignore'}</span>
									{#if conditionLabel(rule.condition)}
										<span class="rounded px-1.5 py-0.5 text-laya-secondary font-semibold uppercase bg-blue-900/50 text-blue-400">{conditionLabel(rule.condition)}</span>
									{/if}
									<span class="font-medium {rule.enabled ? 'text-surface-50' : 'text-surface-500'}">{rule.name}</span>
								</div>
								<p class="mt-1 pl-12 font-mono text-laya-secondary text-surface-400">{conditionSummary(rule.condition)}</p>
							</div>
							<div class="flex items-center gap-1">
								<button class="rounded p-1 text-surface-500 transition-colors hover:text-surface-200" onclick={() => startEdit(i)} aria-label="Edit rule" title="Edit">
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
								</button>
								<button class="rounded p-1 text-surface-500 transition-colors hover:text-red-400" onclick={() => removeRule(i)} aria-label="Remove rule" title="Remove">
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
								</button>
							</div>
						</div>
					</div>
				{/each}
				{#if rules.length === 0}
					<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6 text-center text-surface-500">
						No filter rules configured
					</div>
				{/if}
			</div>

			<!-- Add/Edit form (filter rules) -->
			{#if showAddForm || editingIndex !== null}
				<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4">
					<h3 class="mb-3 text-laya-base font-medium">{editingIndex !== null ? 'Edit Rule' : 'Add Rule'}</h3>
					<div class="space-y-3">
						<div class="flex gap-3">
							<input bind:value={formName} placeholder="Rule name" class="flex-1 rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
							<div class="flex rounded-lg border border-surface-600 overflow-hidden">
								<button
									class="px-3 py-2 text-laya-base font-medium transition-colors {formAction === 'drop' ? 'bg-red-900/60 text-red-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
									onclick={() => (formAction = 'drop')}
								>Ignore</button>
								<button
									class="px-3 py-2 text-laya-base font-medium transition-colors {formAction === 'allow' ? 'bg-green-900/60 text-green-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
									onclick={() => (formAction = 'allow')}
								>Allow</button>
							</div>
						</div>

						{#if formConditions.length > 1}
							<div class="flex items-center gap-2 text-laya-secondary text-surface-400">
								<span>Match</span>
								<div class="flex rounded-lg border border-surface-600 overflow-hidden">
									<button
										class="px-2.5 py-1 text-laya-secondary font-medium transition-colors {formLogic === 'all' ? 'bg-blue-900/60 text-blue-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
										onclick={() => (formLogic = 'all')}
									>ALL conditions</button>
									<button
										class="px-2.5 py-1 text-laya-secondary font-medium transition-colors {formLogic === 'any' ? 'bg-blue-900/60 text-blue-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
										onclick={() => (formLogic = 'any')}
									>ANY condition</button>
								</div>
							</div>
						{/if}

						{#each formConditions as cond, ci}
							<div class="flex items-center gap-2">
								{#if formConditions.length > 1}
									<span class="w-8 text-center text-laya-secondary text-surface-500">
										{#if ci === 0}
											If
										{:else}
											{formLogic === 'all' ? '&' : 'or'}
										{/if}
									</span>
								{/if}
								<div class="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-3">
									<select bind:value={cond.field} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50">
										{#each commonFields as field}
											<option value={field}>{field}</option>
										{/each}
									</select>
									<select bind:value={cond.operator} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50">
										{#each operators as op}
											<option value={op}>{op.replace('_', ' ')}</option>
										{/each}
									</select>
									<input bind:value={cond.value} placeholder="Value" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
								</div>
								{#if formConditions.length > 1}
									<button
										class="rounded p-1 text-surface-500 transition-colors hover:bg-surface-700 hover:text-red-400"
										onclick={() => removeConditionRow(ci)}
										aria-label="Remove condition"
									>
										<svg class="h-4 w-4" viewBox="0 0 16 16" fill="currentColor"><path d="M4.646 4.646a.5.5 0 0 1 .708 0L8 7.293l2.646-2.647a.5.5 0 0 1 .708.708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 0 1 0-.708z"/></svg>
									</button>
								{/if}
							</div>
						{/each}

						<button
							class="text-laya-secondary text-surface-400 transition-colors hover:text-surface-200"
							onclick={addConditionRow}
						>+ Add condition</button>
					</div>
					<div class="mt-3 flex gap-2">
						<button
							class="rounded-lg bg-surface-600 px-4 py-2 text-laya-base font-medium hover:bg-surface-500"
							onclick={editingIndex !== null ? saveEdit : addRule}
							disabled={!formValid}
						>
							{saving ? 'Saving...' : editingIndex !== null ? 'Save' : 'Add'}
						</button>
						<button class="rounded-lg px-4 py-2 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetForm}>
							Cancel
						</button>
					</div>
				</div>
			{:else}
				<button
					class="rounded-lg border border-dashed border-surface-600 px-4 py-2 text-laya-base text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
					onclick={() => (showAddForm = true)}
				>
					+ Add Filter Rule
				</button>
			{/if}
		{/if}
	</div>

	<!-- Divider -->
	<div class="my-8 border-t border-surface-700"></div>

	<!-- ═══════════════ CLASSIFICATION RULES ═══════════════ -->
	<div class="space-y-4">
		<div>
			<h3 class="text-laya-heading font-semibold text-surface-50">Classification Rules</h3>
			<p class="mt-1 text-laya-base text-surface-400">
				Classification rules guide how Laya assigns priority and persona to your cards. Write rules in plain language — they're injected into the AI's instructions. You can also add rules through the "Adjust classification" link on any card.
			</p>
		</div>

		{#if clsError}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-laya-base text-red-300">{clsError}</div>
		{/if}

		{#if clsLoading}
			<div class="text-surface-400 text-laya-base">Loading classification rules...</div>
		{:else}
			<div class="space-y-3">
				{#each clsRules as rule (rule.id)}
					{#if clsEditingId === rule.id}
						<!-- Inline edit form -->
						<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} border-laya-orange/30 p-4 space-y-3">
							<div class="flex gap-3">
								<input
									bind:value={clsFormText}
									placeholder="Rule text"
									class="flex-1 rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500"
								/>
								<Dropdown
									value={clsFormField ?? ''}
									options={clsFieldOptions.map((o) => ({ value: o.value ?? '', label: o.label }))}
									onchange={(v) => { clsFormField = v || null; }}
									placeholder="Field…"
								/>
							</div>
							<div class="flex gap-2">
								<button
									class="rounded-lg {$glassTheme ? 'bg-white/10 hover:bg-white/15' : 'bg-surface-600 hover:bg-surface-500'} px-4 py-2 text-laya-base font-medium disabled:opacity-50"
									onclick={saveClsEdit}
									disabled={!clsFormValid || clsSaving}
								>
									{clsSaving ? 'Saving...' : 'Save'}
								</button>
								<button class="rounded-lg px-4 py-2 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetClsForm}>
									Cancel
								</button>
							</div>
						</div>
					{:else}
						<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4">
							<div class="flex items-start justify-between gap-3">
								<div class="flex items-start gap-3 flex-1">
									<button
										class="relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors {rule.active ? 'bg-green-600' : 'bg-surface-600'}"
										onclick={() => toggleClsRule(rule)}
										aria-label="Toggle rule"
									>
										<span class="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform {rule.active ? 'left-[1.125rem]' : 'left-0.5'}"></span>
									</button>
									<div class="flex-1 min-w-0">
										<div class="flex items-center gap-2 flex-wrap">
											{#if rule.field}
												<span class="rounded px-1.5 py-0.5 text-laya-micro font-semibold uppercase bg-laya-orange/15 text-laya-orange">{rule.field}</span>
											{/if}
											<span class="rounded px-1.5 py-0.5 text-laya-micro font-semibold uppercase
												{rule.source === 'learned' ? 'bg-blue-900/50 text-blue-400' : 'bg-surface-700 text-surface-400'}">
												{rule.source}
											</span>
										</div>
										<p class="mt-1 text-laya-base {rule.active ? 'text-surface-200' : 'text-surface-500'}">{rule.rule_text}</p>
									</div>
								</div>
								<div class="flex items-center gap-1 shrink-0">
									<button class="rounded p-1 text-surface-500 transition-colors hover:text-surface-200" onclick={() => startClsEdit(rule)} aria-label="Edit rule" title="Edit">
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
									</button>
									<button class="rounded p-1 text-surface-500 transition-colors hover:text-red-400" onclick={() => removeClsRule(rule)} aria-label="Remove rule" title="Remove">
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
									</button>
								</div>
							</div>
						</div>
					{/if}
				{/each}
				{#if clsRules.length === 0}
					<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6 text-center text-surface-500">
						No classification rules yet. Add one below or use "Adjust classification" on any card.
					</div>
				{/if}
			</div>

			<!-- Add form (classification rules) -->
			{#if showClsAddForm}
				<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4 space-y-3">
					<div class="flex gap-3">
						<input
							bind:value={clsFormText}
							placeholder='e.g., "Always treat emails from legal@acme.com as HIGH priority"'
							class="flex-1 rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500"
						/>
						<Dropdown
							value={clsFormField ?? ''}
							options={clsFieldOptions.map((o) => ({ value: o.value ?? '', label: o.label }))}
							onchange={(v) => { clsFormField = v || null; }}
							placeholder="Field…"
						/>
					</div>
					<div class="flex gap-2">
						<button
							class="rounded-lg {$glassTheme ? 'bg-white/10 hover:bg-white/15' : 'bg-surface-600 hover:bg-surface-500'} px-4 py-2 text-laya-base font-medium disabled:opacity-50"
							onclick={addClsRule}
							disabled={!clsFormValid || clsSaving}
						>
							{clsSaving ? 'Saving...' : 'Add'}
						</button>
						<button class="rounded-lg px-4 py-2 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetClsForm}>
							Cancel
						</button>
					</div>
				</div>
			{:else if clsEditingId === null}
				<button
					class="rounded-lg border border-dashed border-surface-600 px-4 py-2 text-laya-base text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
					onclick={() => (showClsAddForm = true)}
				>
					+ Add Classification Rule
				</button>
			{/if}
		{/if}
	</div>

	<!-- Processing Rules -->
	<hr class="my-6 border-surface-700" />
	<ProcessingRulesEditor />
{/if}
