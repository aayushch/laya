<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { Rule, RuleCondition, SimpleCondition } from '$lib/api/types';

	const operators: SimpleCondition['operator'][] = ['equals', 'not_equals', 'contains', 'starts_with', 'ends_with', 'in'];
	const commonFields = [
		'actor.email',
		'actor.name',
		'source.platform',
		'source.raw_event_type',
		'subject.type',
		'subject.id',
		'content.metadata.slack_channel',
		'content.metadata.jira_project'
	];

	let rules = $state<Rule[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let editingIndex = $state<number | null>(null);
	let showAddForm = $state(false);

	// Form fields (simple condition only for MVP)
	let formName = $state('');
	let formField = $state('actor.email');
	let formOperator = $state<SimpleCondition['operator']>('contains');
	let formValue = $state('');

	onMount(async () => {
		try {
			const data = await engineApi.getRules();
			rules = data.rules;
		} catch {
			error = 'Failed to load rules configuration';
		} finally {
			loading = false;
		}
	});

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
			return `${condition.field} ${condition.operator} "${condition.value}"`;
		}
		if ('all' in condition) {
			return `ALL of: (${condition.all.map(conditionSummary).join(', ')})`;
		}
		if ('any' in condition) {
			return `ANY of: (${condition.any.map(conditionSummary).join(', ')})`;
		}
		return 'Unknown condition';
	}

	function isSimple(condition: RuleCondition): boolean {
		return 'field' in condition;
	}

	function resetForm() {
		formName = '';
		formField = 'actor.email';
		formOperator = 'contains';
		formValue = '';
		showAddForm = false;
		editingIndex = null;
	}

	async function addRule() {
		rules.push({
			name: formName,
			enabled: true,
			condition: { field: formField, operator: formOperator, value: formValue },
			action: 'drop'
		});
		resetForm();
		await save();
	}

	function startEdit(index: number) {
		const r = rules[index];
		formName = r.name;
		if (isSimple(r.condition)) {
			const c = r.condition as SimpleCondition;
			formField = c.field;
			formOperator = c.operator;
			formValue = typeof c.value === 'string' ? c.value : c.value.join(', ');
		}
		editingIndex = index;
		showAddForm = false;
	}

	async function saveEdit() {
		if (editingIndex !== null) {
			rules[editingIndex] = {
				...rules[editingIndex],
				name: formName,
				condition: { field: formField, operator: formOperator, value: formValue }
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
</script>

{#if loading}
	<div class="text-surface-400">Loading rules...</div>
{:else}
	{#if error}
		<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">{error}</div>
	{/if}

	<!-- Rules list -->
	<div class="space-y-3">
		{#each rules as rule, i}
			<div class="rounded-xl border border-surface-700 bg-surface-800 p-4">
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
							<span class="font-medium {rule.enabled ? 'text-surface-50' : 'text-surface-500'}">{rule.name}</span>
						</div>
						<p class="mt-1 pl-12 font-mono text-xs text-surface-400">{conditionSummary(rule.condition)}</p>
					</div>
					<div class="flex gap-2">
						{#if isSimple(rule.condition)}
							<button class="text-sm text-surface-400 hover:text-surface-100" onclick={() => startEdit(i)}>Edit</button>
						{/if}
						<button class="text-sm text-red-400 hover:text-red-300" onclick={() => removeRule(i)}>Remove</button>
					</div>
				</div>
			</div>
		{/each}
		{#if rules.length === 0}
			<div class="rounded-xl border border-surface-700 bg-surface-800 p-6 text-center text-surface-500">
				No filter rules configured
			</div>
		{/if}
	</div>

	<!-- Add/Edit form -->
	{#if showAddForm || editingIndex !== null}
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-4">
			<h3 class="mb-3 text-sm font-medium">{editingIndex !== null ? 'Edit Rule' : 'Add Rule'}</h3>
			<div class="space-y-3">
				<input bind:value={formName} placeholder="Rule name" class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
				<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
					<select bind:value={formField} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50">
						{#each commonFields as field}
							<option value={field}>{field}</option>
						{/each}
					</select>
					<select bind:value={formOperator} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50">
						{#each operators as op}
							<option value={op}>{op.replace('_', ' ')}</option>
						{/each}
					</select>
					<input bind:value={formValue} placeholder="Value" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
				</div>
			</div>
			<div class="mt-3 flex gap-2">
				<button
					class="rounded-lg bg-surface-600 px-4 py-2 text-sm font-medium hover:bg-surface-500"
					onclick={editingIndex !== null ? saveEdit : addRule}
					disabled={!formName || !formValue}
				>
					{saving ? 'Saving...' : editingIndex !== null ? 'Save' : 'Add'}
				</button>
				<button class="rounded-lg px-4 py-2 text-sm text-surface-400 hover:text-surface-200" onclick={resetForm}>
					Cancel
				</button>
			</div>
		</div>
	{:else}
		<button
			class="rounded-lg border border-dashed border-surface-600 px-4 py-2 text-sm text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
			onclick={() => (showAddForm = true)}
		>
			+ Add Rule
		</button>
	{/if}
{/if}
