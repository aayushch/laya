<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import type { ProcessingRule, ProcessingRuleAction, ProcessingCondition, ProcessingRuleOperator, ProcessingSimpleCondition, ComposePlatform } from '$lib/api/types';

	let tooltip = $state<{ text: string; top: number; left: number } | null>(null);
	function showTip(el: HTMLElement, text: string) {
		const rect = el.getBoundingClientRect();
		tooltip = { text, top: rect.bottom + 4, left: rect.left + rect.width / 2 };
	}
	function hideTip() { tooltip = null; }

	// --- State ---
	let rules = $state<ProcessingRule[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let saving = $state(false);
	let confirmDeleteId = $state<number | null>(null);
	let formMode = $state<'closed' | 'add' | { edit: number }>('closed');
	let showAddForm = $derived(formMode === 'add');
	let editingId = $derived(typeof formMode === 'object' && 'edit' in formMode ? formMode.edit : null);

	// --- Form state ---
	let formName = $state('');
	let formDescription = $state('');
	let formLogic = $state<'all' | 'any'>('all');
	let formConditions = $state<ConditionRow[]>([{ field: 'event.source.platform', operator: 'equals', value: '' }]);
	let formActions = $state<ActionFormRow[]>([{ type: 'set_status', config: { status: 'dismissed' } }]);
	let formRateLimit = $state(0);
	let formCooldownSecs = $state(0);
	let formMaxDaily = $state(0);
	let showAdvanced = $state(false);

	// --- Preview state ---
	let previewing = $state(false);
	let previewResult = $state<{ match_count: number; sample_cards: Array<{ card_id: string; header: string; priority: string }> } | null>(null);

	interface ConditionRow {
		field: string;
		operator: ProcessingRuleOperator;
		value: string;
	}

	interface ActionFormRow {
		type: ProcessingRuleAction['type'];
		config: Record<string, string>;
	}

	// --- Field groups for the condition builder ---
	const fieldGroups = [
		{
			label: 'Event',
			fields: [
				'event.source.platform', 'event.source.raw_event_type', 'event.source.connection_id',
				'event.actor.name', 'event.actor.email', 'event.actor.platform_handle',
				'event.subject.type', 'event.subject.id', 'event.subject.title',
				'event.content.body',
			]
		},
		{
			label: 'Classification',
			fields: [
				'classification.persona', 'classification.priority', 'classification.category',
				'classification.confidence', 'classification.requires_research',
			]
		},
		{
			label: 'Context',
			fields: [
				'context.actor_relationship', 'context.entity_card_count',
				'context.is_carry_forward', 'context.hour_of_day', 'context.day_of_week',
			]
		},
	];

	const allFields = fieldGroups.flatMap(g => g.fields);

	const stringOperators: ProcessingRuleOperator[] = ['equals', 'not_equals', 'contains', 'not_contains', 'starts_with', 'ends_with', 'in', 'not_in', 'matches'];
	const numericOperators: ProcessingRuleOperator[] = ['equals', 'not_equals', 'gt', 'gte', 'lt', 'lte'];
	const existsOperators: ProcessingRuleOperator[] = ['exists', 'not_exists'];

	function operatorsForField(field: string): ProcessingRuleOperator[] {
		if (field.includes('confidence') || field.includes('card_count') || field.includes('hour_of_day') || field.includes('day_of_week')) return [...numericOperators, ...existsOperators];
		if (field.includes('priority')) return [...stringOperators, ...numericOperators];
		return [...stringOperators, ...existsOperators];
	}

	const operatorLabels: Record<string, string> = {
		equals: '=', not_equals: '!=', contains: 'contains', not_contains: 'not contains',
		starts_with: 'starts with', ends_with: 'ends with', in: 'in', not_in: 'not in',
		matches: 'matches regex', gt: '>', gte: '>=', lt: '<', lte: '<=',
		exists: 'exists', not_exists: 'not exists',
	};

	const actionTypes = [
		{ value: 'set_status', label: 'Set Status' },
		{ value: 'set_priority', label: 'Set Priority' },
		{ value: 'bookmark', label: 'Bookmark' },
		{ value: 'run_entity_agent', label: 'Run Agent' },
		{ value: 'execute_egress', label: 'Execute Egress Action' },
		{ value: 'send_notification', label: 'Send Notification' },
	];

	const hasValidConditions = $derived(
		formConditions.some(c =>
			['exists', 'not_exists'].includes(c.operator) || (c.value && c.value.trim() !== '')
		)
	);

	// --- Auto-disable threshold setting ---
	let autoDisableThreshold = $state(5);

	async function saveThreshold() {
		try {
			const result = await engineApi.updateProcessingRulesSettings({ auto_disable_threshold: autoDisableThreshold });
			autoDisableThreshold = result.auto_disable_threshold;
		} catch {
			error = 'Failed to save auto-disable threshold';
		}
	}

	// --- Field options for smart dropdowns ---
	let fieldOptions = $state<Record<string, string[]>>({});

	const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
	const hourLabels = Array.from({ length: 24 }, (_, i) => {
		const h = i % 12 || 12;
		const ampm = i < 12 ? 'AM' : 'PM';
		return { value: String(i), label: `${h}:00 ${ampm}` };
	});

	function getFieldOptions(field: string): string[] | null {
		if (fieldOptions[field]) return fieldOptions[field];
		if (field === 'context.day_of_week') return dayNames.map((_, i) => String(i));
		if (field === 'context.hour_of_day') return hourLabels.map(h => h.value);
		if (field === 'classification.requires_research' || field === 'context.is_carry_forward') return ['true', 'false'];
		return null;
	}

	function getOptionLabel(field: string, value: string): string {
		if (field === 'context.day_of_week') {
			const idx = parseInt(value);
			return dayNames[idx] ?? value;
		}
		if (field === 'context.hour_of_day') {
			const h = hourLabels[parseInt(value)];
			return h ? h.label : value;
		}
		return value;
	}

	// --- Egress platforms registry ---
	let composePlatforms = $state<ComposePlatform[]>([]);

	const egressActionsForPlatform = $derived.by(() => {
		const map = new Map<string, ComposePlatform>();
		for (const p of composePlatforms) map.set(p.id, p);
		return map;
	});

	// --- Load ---
	onMount(async () => {
		try {
			const data = await engineApi.listProcessingRules();
			rules = data.rules;
		} catch {
			error = 'Failed to load processing rules';
		} finally {
			loading = false;
		}
		try {
			const cp = await engineApi.getComposePlatforms();
			composePlatforms = cp.platforms;
		} catch { /* egress platforms unavailable */ }
		try {
			fieldOptions = await engineApi.getProcessingRuleFieldOptions();
		} catch { /* field options unavailable */ }
		try {
			const s = await engineApi.getProcessingRulesSettings();
			autoDisableThreshold = s.auto_disable_threshold;
		} catch { /* settings unavailable, keep default */ }
	});

	// --- CRUD ---
	function resetForm() {
		formName = '';
		formDescription = '';
		formLogic = 'all';
		formConditions = [{ field: 'event.source.platform', operator: 'equals', value: '' }];
		formActions = [{ type: 'set_status', config: { status: 'dismissed' } }];
		formRateLimit = 0;
		formCooldownSecs = 0;
		formMaxDaily = 0;
		showAdvanced = false;
		previewResult = null;
	}

	function buildCondition(): ProcessingCondition {
		const simples: ProcessingSimpleCondition[] = formConditions.map(c => ({
			field: c.field,
			operator: c.operator,
			value: ['exists', 'not_exists'].includes(c.operator) ? null : c.value,
		}));
		if (simples.length === 1) return simples[0];
		return formLogic === 'all' ? { all: simples } : { any: simples };
	}

	function buildActions(): ProcessingRuleAction[] {
		return formActions.map(a => {
			switch (a.type) {
				case 'set_status': return { type: 'set_status' as const, status: (a.config.status || 'dismissed') as 'dismissed' | 'archived' | 'done', reason: a.config.reason || undefined };
				case 'set_priority': return { type: 'set_priority' as const, priority: (a.config.priority || 'HIGH') as 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' };
				case 'bookmark': return { type: 'bookmark' as const };
				case 'run_entity_agent': return { type: 'run_entity_agent' as const, prompt_template: a.config.prompt_template || undefined };
				case 'execute_egress': {
					const payload: Record<string, string> = {};
					for (const [k, v] of Object.entries(a.config)) {
						if (['platform', 'action_type', 'connection_id', 'payload_json'].includes(k)) continue;
						payload[k] = v;
					}
					if (a.config.payload_json) {
						try {
							const parsed = JSON.parse(a.config.payload_json);
							if (typeof parsed === 'object' && parsed !== null) {
								for (const [k, v] of Object.entries(parsed)) {
									payload[k] = String(v);
								}
							}
						} catch {
							error = 'Invalid JSON in egress payload';
							return { type: 'execute_egress' as const, platform: '', action_type: '', payload_template: {} };
						}
					}
					return { type: 'execute_egress' as const, platform: a.config.platform || '', action_type: a.config.action_type || '', payload_template: payload, connection_id: a.config.connection_id || undefined };
				}
				case 'send_notification': return { type: 'send_notification' as const, title_template: a.config.title_template || '', body_template: a.config.body_template || '' };
				default: return { type: 'bookmark' as const };
			}
		});
	}

	function loadIntoForm(rule: ProcessingRule) {
		formName = rule.name;
		formDescription = rule.description || '';
		formRateLimit = rule.rate_limit;
		formCooldownSecs = rule.cooldown_secs;
		formMaxDaily = rule.max_daily;
		showAdvanced = rule.rate_limit > 0 || rule.cooldown_secs > 0 || rule.max_daily > 0;
		previewResult = null;

		// Parse condition
		const cond = rule.condition;
		if ('all' in cond) {
			formLogic = 'all';
			formConditions = (cond.all as ProcessingSimpleCondition[]).map(c => ({ field: c.field, operator: c.operator, value: String(c.value ?? '') }));
		} else if ('any' in cond) {
			formLogic = 'any';
			formConditions = (cond.any as ProcessingSimpleCondition[]).map(c => ({ field: c.field, operator: c.operator, value: String(c.value ?? '') }));
		} else if ('field' in cond) {
			formLogic = 'all';
			formConditions = [{ field: (cond as ProcessingSimpleCondition).field, operator: (cond as ProcessingSimpleCondition).operator, value: String((cond as ProcessingSimpleCondition).value ?? '') }];
		} else {
			formLogic = 'all';
			formConditions = [{ field: 'event.source.platform', operator: 'equals', value: '' }];
		}

		// Parse actions
		formActions = rule.actions.map(a => {
			const config: Record<string, string> = {};
			if (a.type === 'set_status') { config.status = a.status; if (a.reason) config.reason = a.reason; }
			else if (a.type === 'set_priority') { config.priority = a.priority; }
			else if (a.type === 'run_entity_agent') { if (a.prompt_template) config.prompt_template = a.prompt_template; }
			else if (a.type === 'execute_egress') { config.platform = a.platform; config.action_type = a.action_type; if (a.connection_id) config.connection_id = a.connection_id; Object.entries(a.payload_template).forEach(([k, v]) => { config[k] = v; }); }
			else if (a.type === 'send_notification') { config.title_template = a.title_template; config.body_template = a.body_template; }
			return { type: a.type, config };
		});
	}

	async function saveRule() {
		if (!formName.trim()) return;
		saving = true;
		error = null;
		try {
			const condition = buildCondition();
			const actions = buildActions();
			if (editingId !== null) {
				const updated = await engineApi.updateProcessingRule(editingId, {
					name: formName, description: formDescription || null,
					condition, actions,
					rate_limit: formRateLimit, cooldown_secs: formCooldownSecs, max_daily: formMaxDaily,
				});
				rules = rules.map(r => r.id === editingId ? updated : r);
				formMode = 'closed';
			} else {
				const created = await engineApi.createProcessingRule({
					name: formName, description: formDescription || undefined,
					condition, actions,
					rate_limit: formRateLimit, cooldown_secs: formCooldownSecs, max_daily: formMaxDaily,
				});
				rules = [...rules, created];
				formMode = 'closed';
			}
			resetForm();
		} catch (e: any) {
			error = e?.message || 'Failed to save rule';
		} finally {
			saving = false;
		}
	}

	async function deleteRule(id: number) {
		try {
			await engineApi.deleteProcessingRule(id);
			rules = rules.filter(r => r.id !== id);
		} catch {
			error = 'Failed to delete rule';
		}
	}

	async function toggleRule(id: number) {
		try {
			const result = await engineApi.toggleProcessingRule(id);
			rules = rules.map(r => r.id === id ? { ...r, enabled: result.enabled, error_count: result.enabled ? 0 : r.error_count } : r);
		} catch {
			error = 'Failed to toggle rule';
		}
	}

	async function testCondition() {
		previewing = true;
		previewResult = null;
		try {
			const condition = buildCondition();
			previewResult = await engineApi.previewProcessingRuleMatches(condition);
		} catch {
			previewResult = null;
		} finally {
			previewing = false;
		}
	}

	function conditionSummary(cond: ProcessingCondition): string {
		if ('field' in cond) {
			const c = cond as ProcessingSimpleCondition;
			const fieldShort = c.field.split('.').pop() || c.field;
			const op = operatorLabels[c.operator] || c.operator;
			if (['exists', 'not_exists'].includes(c.operator)) return `${fieldShort} ${op}`;
			return `${fieldShort} ${op} "${c.value}"`;
		}
		if ('all' in cond) return (cond.all as ProcessingCondition[]).map(conditionSummary).join(' AND ');
		if ('any' in cond) return (cond.any as ProcessingCondition[]).map(conditionSummary).join(' OR ');
		if ('not' in cond) return `NOT (${conditionSummary((cond as any).not)})`;
		return '?';
	}

	function actionSummary(a: ProcessingRuleAction): string {
		switch (a.type) {
			case 'set_status': return `Set ${a.status}`;
			case 'set_priority': return `Priority → ${a.priority}`;
			case 'bookmark': return 'Bookmark';
			case 'run_entity_agent': return 'Run Agent';
			case 'execute_egress': return `${a.platform}/${a.action_type}`;
			case 'send_notification': return 'Notify';
			default: return String((a as any).type);
		}
	}

	function timeAgo(dateStr?: string | null): string {
		if (!dateStr) return 'never';
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const diff = Date.now() - new Date(utcStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	function fieldLabel(field: string): string {
		return field.split('.').pop() || field;
	}
</script>

{#if loading}
	<div class="text-surface-400">Loading processing rules...</div>
{:else}
	{#if error}
		<div class="flex items-start gap-2 rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">
			<span class="flex-1">{error}</span>
			<button class="shrink-0 text-red-400 hover:text-red-200" onclick={() => (error = null)} aria-label="Dismiss error">
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
			</button>
		</div>
	{/if}

	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4">
		<div class="mb-4 flex items-center justify-between">
			<div>
				<h3 class="text-sm font-medium">Processing Rules</h3>
				<p class="text-xs text-surface-400">Automate actions when events match specific conditions</p>
			</div>
		</div>

		<!-- Auto-disable threshold setting -->
		<div class="mb-4 flex items-center gap-3">
			<label for="auto-disable-threshold" class="text-xs text-surface-400">Auto-disable rules after</label>
			<input id="auto-disable-threshold" type="number" bind:value={autoDisableThreshold} min="1" max="100"
				class="w-16 rounded-lg border border-surface-600 bg-surface-900 px-2 py-1 text-sm text-surface-50"
				onchange={saveThreshold} />
			<span class="text-xs text-surface-500">consecutive errors</span>
		</div>

		<!-- Rule list -->
		<div class="space-y-2">
			{#each rules as rule (rule.id)}
				{#if editingId === rule.id}
					{@render ruleForm()}
				{:else}
					<div class="rounded-lg border px-4 py-3 {rule.enabled ? ($glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-600 bg-surface-900') : 'border-surface-700/50 bg-surface-900/50 opacity-60'}">
						<div class="flex items-center gap-3">
							<button
								class="flex h-4 w-4 items-center justify-center rounded border transition-colors {rule.enabled ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-500'}"
								onclick={() => toggleRule(rule.id)}
							>
								{#if rule.enabled}
									<svg class="h-3 w-3 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
								{/if}
							</button>
							<div class="flex-1 min-w-0">
								<div class="flex items-center gap-2">
									<span class="text-sm font-medium text-surface-100 truncate">{rule.name}</span>
									{#if rule.error_count > 0}
										<span class="rounded bg-red-900/50 px-1.5 py-0.5 text-[10px] font-medium text-red-300">{rule.error_count} errors</span>
									{/if}
								</div>
								<div class="mt-0.5 text-[11px] text-surface-500 truncate">
									<span class="text-surface-400">WHEN</span> {conditionSummary(rule.condition)}
									<span class="mx-1 text-surface-600">→</span>
									<span class="text-surface-400">THEN</span> {rule.actions.map(actionSummary).join(', ')}
								</div>
								<div class="mt-1 flex items-center gap-3 text-[10px] text-surface-600">
									<span>Fired {rule.fire_count}x</span>
									<span>Last: {timeAgo(rule.last_fired_at)}</span>
									{#if rule.last_error}
										<span class="text-red-400 truncate max-w-[200px]" title={rule.last_error}>Error: {rule.last_error}</span>
									{/if}
								</div>
							</div>
							<div class="flex items-center gap-1">
								<button
									class="rounded p-1 text-surface-500 transition-colors hover:text-surface-200"
									onclick={() => { formMode = { edit: rule.id }; loadIntoForm(rule); }}
									aria-label="Edit rule"
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
								</button>
								{#if confirmDeleteId === rule.id}
									<button
										class="rounded px-2 py-0.5 text-[10px] font-medium bg-red-900/50 text-red-300 transition-colors hover:bg-red-800/60"
										onclick={() => { deleteRule(rule.id); confirmDeleteId = null; }}
									>Delete</button>
									<button
										class="rounded px-1.5 py-0.5 text-[10px] text-surface-400 hover:text-surface-200"
										onclick={() => (confirmDeleteId = null)}
									>Cancel</button>
								{:else}
									<button
										class="rounded p-1 text-surface-500 transition-colors hover:text-red-400"
										onclick={() => (confirmDeleteId = rule.id)}
										aria-label="Delete rule"
									>
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
									</button>
								{/if}
							</div>
						</div>
					</div>
				{/if}
			{/each}
		</div>

		<!-- Add form -->
		{#if showAddForm}
			<div class="mt-3">
				{@render ruleForm()}
			</div>
		{:else if editingId === null}
			<button
				class="mt-3 rounded-lg border border-dashed border-surface-600 px-4 py-2 text-sm text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
				onclick={() => { resetForm(); formMode = 'add'; }}
			>
				+ Add Processing Rule
			</button>
		{/if}
	</div>
{/if}

{#if tooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {tooltip.top}px; left: {tooltip.left}px;"
	>
		{tooltip.text}
	</span>
{/if}

{#snippet ruleForm()}
	<div class="rounded-lg border border-laya-orange/30 {$glassTheme ? 'bg-white/[0.03]' : 'bg-surface-850'} p-4 space-y-4">
		<!-- Name -->
		<div>
			<label class="mb-1 block text-xs font-medium text-surface-300">
				Rule Name
				<input
					bind:value={formName}
					placeholder="e.g. Auto-dismiss low-priority calendar"
					class="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500 focus:border-laya-orange focus:outline-none"
				/>
			</label>
		</div>

		<!-- Description (optional) -->
		<div>
			<label class="mb-1 block text-xs font-medium text-surface-300">
				Description <span class="text-surface-500">(optional)</span>
				<input
					bind:value={formDescription}
					placeholder="What does this rule do?"
					class="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500 focus:border-laya-orange/50 focus:outline-none"
				/>
			</label>
		</div>

		<!-- WHEN section -->
		<div>
			<div class="mb-2 flex items-center gap-2">
				<span class="text-xs font-semibold uppercase tracking-wider text-surface-400">When</span>
				{#if formConditions.length > 1}
					<div class="inline-flex overflow-hidden rounded-lg border border-surface-600">
						<button
							class="px-2.5 py-1 text-xs font-medium transition-colors {formLogic === 'all' ? 'bg-blue-900/60 text-blue-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
							onclick={() => (formLogic = 'all')}
						>ALL match</button>
						<button
							class="px-2.5 py-1 text-xs font-medium transition-colors {formLogic === 'any' ? 'bg-blue-900/60 text-blue-300' : 'bg-surface-900 text-surface-400 hover:text-surface-200'}"
							onclick={() => (formLogic = 'any')}
						>ANY match</button>
					</div>
				{/if}
			</div>

			<div class="space-y-2">
				{#each formConditions as cond, i}
					<div class="flex items-center gap-2">
						<div class="grid flex-1 grid-cols-[2fr_1fr_2fr] gap-2">
							<select
								bind:value={cond.field}
								class="h-[38px] rounded-lg border border-surface-600 bg-surface-900 px-3 text-sm text-surface-50"
							>
								{#each fieldGroups as group}
									<optgroup label={group.label}>
										{#each group.fields as field}
											<option value={field}>{fieldLabel(field)}</option>
										{/each}
									</optgroup>
								{/each}
							</select>
							<select
								bind:value={cond.operator}
								class="h-[38px] rounded-lg border border-surface-600 bg-surface-900 px-3 text-sm text-surface-50"
							>
								{#each operatorsForField(cond.field) as op}
									<option value={op}>{operatorLabels[op]}</option>
								{/each}
							</select>
							{#if ['exists', 'not_exists'].includes(cond.operator)}
								<div></div>
							{:else}
								{@const options = getFieldOptions(cond.field)}
								{#if options}
									<select
										bind:value={cond.value}
										class="h-[38px] rounded-lg border border-surface-600 bg-surface-900 px-3 text-sm text-surface-50"
									>
										<option value="">Select...</option>
										{#each options as opt}
											<option value={opt}>{getOptionLabel(cond.field, opt)}</option>
										{/each}
									</select>
								{:else}
									<input
										bind:value={cond.value}
										placeholder="Value"
										class="h-[38px] rounded-lg border border-surface-600 bg-surface-900 px-3 text-sm text-surface-50 placeholder-surface-500"
									/>
								{/if}
							{/if}
						</div>
						{#if formConditions.length > 1}
							<button
								class="rounded p-1 text-surface-500 transition-colors hover:bg-surface-700 hover:text-red-400"
								onclick={() => { formConditions = formConditions.filter((_, j) => j !== i); }}
								aria-label="Remove condition"
							>
								<svg class="h-4 w-4" viewBox="0 0 16 16" fill="currentColor"><path d="M4.646 4.646a.5.5 0 0 1 .708 0L8 7.293l2.646-2.647a.5.5 0 0 1 .708.708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 0 1 0-.708z"/></svg>
							</button>
						{/if}
					</div>
				{/each}
			</div>

			<button
				class="mt-2 text-xs text-surface-400 hover:text-surface-200"
				onclick={() => { formConditions = [...formConditions, { field: 'event.source.platform', operator: 'equals', value: '' }]; }}
			>+ Add condition</button>
		</div>

		<!-- THEN section -->
		<div>
			<span class="mb-2 block text-xs font-semibold uppercase tracking-wider text-surface-400">Then</span>

			<div class="space-y-3">
				{#each formActions as action, i}
					{@const selectedPlatform = egressActionsForPlatform.get(action.config.platform ?? '')}
					{@const platformActions = selectedPlatform?.actions ?? []}
					{@const selectedAction = platformActions.find(a => a.action_type === action.config.action_type)}
					<div class="rounded-lg border border-surface-700 {$glassTheme ? 'bg-white/[0.02]' : 'bg-surface-900/50'} p-4">
						<div class="flex items-center gap-2">
							<select
								bind:value={action.type}
								class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50"
								onchange={() => { action.config = action.type === 'set_status' ? { status: 'dismissed' } : action.type === 'set_priority' ? { priority: 'HIGH' } : {}; }}
							>
								{#each actionTypes as at}
									<option value={at.value}>{at.label}</option>
								{/each}
							</select>
							{#if formActions.length > 1}
								<button
									class="rounded p-1 text-surface-500 transition-colors hover:bg-surface-700 hover:text-red-400"
									onclick={() => { formActions = formActions.filter((_, j) => j !== i); }}
									aria-label="Remove action"
								>
									<svg class="h-4 w-4" viewBox="0 0 16 16" fill="currentColor"><path d="M4.646 4.646a.5.5 0 0 1 .708 0L8 7.293l2.646-2.647a.5.5 0 0 1 .708.708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 0 1 0-.708z"/></svg>
								</button>
							{/if}
						</div>

						<!-- Action-specific config -->
						{#if action.type === 'set_status'}
							<div class="mt-3 grid grid-cols-2 gap-2">
								<select bind:value={action.config.status} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50">
									<option value="dismissed">Dismiss</option>
									<option value="archived">Archive</option>
									<option value="done">Mark Done</option>
								</select>
								<input bind:value={action.config.reason} placeholder="Reason (optional)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
							</div>
						{:else if action.type === 'set_priority'}
							<div class="mt-3">
								<select bind:value={action.config.priority} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50">
									<option value="LOW">LOW</option>
									<option value="MEDIUM">MEDIUM</option>
									<option value="HIGH">HIGH</option>
									<option value="CRITICAL">CRITICAL</option>
								</select>
							</div>
						{:else if action.type === 'bookmark'}
							<p class="mt-3 text-sm text-surface-500">Card will be bookmarked automatically.</p>
						{:else if action.type === 'run_entity_agent'}
							<textarea
								bind:value={action.config.prompt_template}
								placeholder="Agent prompt (optional). Use variables like: event.subject.title, classification.priority"
								rows="2"
								class="mt-3 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500"
							></textarea>
						{:else if action.type === 'execute_egress'}
							<div class="mt-3 grid grid-cols-2 gap-2">
								<select
									bind:value={action.config.platform}
									class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50"
									onchange={() => { action.config.action_type = ''; }}
								>
									<option value="">Select platform</option>
									{#each composePlatforms as p}
										<option value={p.id}>{p.label}</option>
									{/each}
								</select>
								<select
									bind:value={action.config.action_type}
									class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50"
									disabled={!action.config.platform}
								>
									<option value="">Select action</option>
									{#each platformActions as pa}
										<option value={pa.action_type}>{pa.label}</option>
									{/each}
								</select>
							</div>
							{#if selectedAction && selectedAction.fields.length > 0}
								<div class="mt-2 space-y-2">
									{#each selectedAction.fields as field}
										{#if field.type === 'textarea'}
											<textarea
												bind:value={action.config[field.name]}
												placeholder={field.placeholder || field.label}
												rows="2"
												class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500"
											></textarea>
										{:else if field.type === 'select' && field.options}
											<select
												bind:value={action.config[field.name]}
												class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50"
											>
												<option value="">{field.placeholder || field.label}</option>
												{#each field.options as opt}
													<option value={opt}>{opt}</option>
												{/each}
											</select>
										{:else}
											<input
												bind:value={action.config[field.name]}
												placeholder={field.placeholder || field.label}
												class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500"
											/>
										{/if}
									{/each}
								</div>
							{/if}
						{:else if action.type === 'send_notification'}
							<div class="mt-3 space-y-2">
								<input bind:value={action.config.title_template} placeholder="Notification title" class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
								<textarea
									bind:value={action.config.body_template}
									placeholder="Notification body"
									rows="2"
									class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500"
								></textarea>
							</div>
						{/if}
					</div>
				{/each}
			</div>

			<button
				class="mt-2 text-xs text-surface-400 hover:text-surface-200"
				onclick={() => { formActions = [...formActions, { type: 'set_status', config: { status: 'dismissed' } }]; }}
			>+ Add action</button>
		</div>

		<!-- Advanced (rate limiting) -->
		<div>
			<button
				class="text-xs text-surface-500 hover:text-surface-300"
				onclick={() => (showAdvanced = !showAdvanced)}
			>
				{showAdvanced ? 'Hide' : 'Show'} advanced options
			</button>
			{#if showAdvanced}
				<div class="mt-2 grid grid-cols-3 gap-3">
					<div>
						<label for="rule-rate-limit" class="mb-1 flex items-center gap-1 text-xs text-surface-400">
							Max per hour
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<span class="cursor-help" onmouseenter={(e) => showTip(e.currentTarget as HTMLElement, 'Maximum times this rule can fire per hour globally')} onmouseleave={hideTip}>
								<svg class="h-3 w-3 text-surface-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-3a1 1 0 00-.867.5 1 1 0 11-1.731-1A3 3 0 0113 8a3.001 3.001 0 01-2 2.83V11a1 1 0 11-2 0v-1a1 1 0 011-1 1 1 0 100-2zm0 8a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" /></svg>
							</span>
						</label>
						<input id="rule-rate-limit" type="number" bind:value={formRateLimit} min="0" class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50" />
						<p class="mt-1 text-[11px] text-surface-600">0 = unlimited</p>
					</div>
					<div>
						<label for="rule-cooldown" class="mb-1 flex items-center gap-1 text-xs text-surface-400">
							Entity cooldown
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<span class="cursor-help" onmouseenter={(e) => showTip(e.currentTarget as HTMLElement, 'Minimum seconds between firings for the same entity (e.g. same PR)')} onmouseleave={hideTip}>
								<svg class="h-3 w-3 text-surface-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-3a1 1 0 00-.867.5 1 1 0 11-1.731-1A3 3 0 0113 8a3.001 3.001 0 01-2 2.83V11a1 1 0 11-2 0v-1a1 1 0 011-1 1 1 0 100-2zm0 8a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" /></svg>
							</span>
						</label>
						<input id="rule-cooldown" type="number" bind:value={formCooldownSecs} min="0" class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50" />
						<p class="mt-1 text-[11px] text-surface-600">In seconds, 0 = none</p>
					</div>
					<div>
						<label for="rule-max-daily" class="mb-1 flex items-center gap-1 text-xs text-surface-400">
							Max per day
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<span class="cursor-help" onmouseenter={(e) => showTip(e.currentTarget as HTMLElement, 'Maximum total firings per calendar day. Prevents runaway rules.')} onmouseleave={hideTip}>
								<svg class="h-3 w-3 text-surface-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-3a1 1 0 00-.867.5 1 1 0 11-1.731-1A3 3 0 0113 8a3.001 3.001 0 01-2 2.83V11a1 1 0 11-2 0v-1a1 1 0 011-1 1 1 0 100-2zm0 8a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" /></svg>
							</span>
						</label>
						<input id="rule-max-daily" type="number" bind:value={formMaxDaily} min="0" class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50" />
						<p class="mt-1 text-[11px] text-surface-600">0 = unlimited</p>
					</div>
				</div>
			{/if}
		</div>

		<!-- Preview -->
		{#if previewResult}
			<div class="rounded-md border border-surface-700 bg-surface-900/50 p-3">
				<p class="text-xs text-surface-300">
					<span class="font-medium text-laya-orange">{previewResult.match_count}</span> cards matched in the last 7 days
				</p>
				{#if previewResult.sample_cards.length > 0}
					<div class="mt-1.5 space-y-1">
						{#each previewResult.sample_cards as card}
							<div class="flex items-center gap-2 text-[11px] text-surface-400">
								<span class="rounded bg-surface-700 px-1 py-0.5 text-[10px]">{card.priority}</span>
								<span class="truncate">{card.header}</span>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Actions -->
		<div class="flex items-center gap-2 pt-2 border-t border-surface-700/50">
			<button
				class="rounded-md bg-laya-orange/20 px-4 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50"
				onclick={saveRule}
				disabled={saving || !formName.trim()}
			>
				{saving ? 'Saving...' : editingId !== null ? 'Update Rule' : 'Create Rule'}
			</button>
			<button
				class="rounded-md border border-surface-600 px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200 disabled:opacity-50"
				onclick={testCondition}
				disabled={previewing || !hasValidConditions}
			>
				{previewing ? 'Testing...' : 'Test Condition'}
			</button>
			<button
				class="ml-auto text-xs text-surface-400 hover:text-surface-200"
				onclick={() => { formMode = 'closed'; resetForm(); }}
			>
				Cancel
			</button>
		</div>
	</div>
{/snippet}
