<script lang="ts">
	let {
		byFeature,
		byStep
	}: {
		byFeature: Record<string, number>;
		byStep: Record<string, number>;
	} = $props();

	// Maps step names to their parent feature — mirrors STEP_TO_FEATURE in budget.py
	const STEP_TO_FEATURE: Record<string, string> = {
		route: 'Pulse',
		stage: 'Pulse',
		emit: 'Pulse',
		entity_confirm: 'Pulse',
		context_confirm: 'Pulse',
		context_learn: 'Pulse',
		learn: 'Pulse',
		summarize: 'Pulse',
		worker: 'Pulse',
		trace: 'Coherence',
		trace_filter: 'Coherence',
		trace_summary: 'Coherence',
		omni_resynthesis: 'Omni',
		chat: 'Chat',
		briefing: 'Briefing',
		egress_draft: 'Egress',
		execute: 'Egress',
		lifecycle: 'System',
		recovery: 'System'
	};

	// Human-readable labels for step names
	const STEP_LABELS: Record<string, string> = {
		route: 'Routing',
		stage: 'Staging',
		emit: 'Formatting',
		entity_confirm: 'Entity Resolution',
		context_confirm: 'Context Association',
		context_learn: 'Context Learning',
		learn: 'Learning',
		summarize: 'Summarization',
		worker: 'Worker',
		trace: 'Narrative',
		trace_filter: 'Relevance Filter',
		trace_summary: 'Summary',
		omni_resynthesis: 'Synthesis',
		chat: 'Chat',
		briefing: 'Briefing',
		egress_draft: 'Draft',
		execute: 'Execution',
		lifecycle: 'Lifecycle',
		recovery: 'Recovery'
	};

	// Build features sorted by cost descending, each with sorted child steps
	const features = $derived.by(() => {
		const entries = Object.entries(byFeature)
			.map(([name, cost]) => {
				// Collect steps belonging to this feature
				const steps = Object.entries(byStep)
					.filter(([step]) => (STEP_TO_FEATURE[step] ?? 'Other') === name)
					.map(([step, stepCost]) => ({
						key: step,
						label: STEP_LABELS[step] ?? step,
						cost: stepCost
					}))
					.sort((a, b) => b.cost - a.cost);
				return { name, cost, steps };
			})
			.sort((a, b) => b.cost - a.cost);

		// Include steps that map to "Other" if any
		const otherSteps = Object.entries(byStep)
			.filter(([step]) => !(STEP_TO_FEATURE[step]) && !entries.some((f) => f.name === 'Other'))
			.map(([step, stepCost]) => ({
				key: step,
				label: STEP_LABELS[step] ?? step,
				cost: stepCost
			}))
			.sort((a, b) => b.cost - a.cost);

		if (otherSteps.length > 0 && !entries.some((f) => f.name === 'Other')) {
			const otherCost = otherSteps.reduce((s, st) => s + st.cost, 0);
			entries.push({ name: 'Other', cost: otherCost, steps: otherSteps });
			entries.sort((a, b) => b.cost - a.cost);
		}

		return entries;
	});

	const maxValue = $derived(Math.max(...features.map((f) => f.cost)) || 1);
	const total = $derived(features.reduce((s, f) => s + f.cost, 0));

	let expanded: Record<string, boolean> = $state({});

	function toggle(feature: string) {
		expanded = { ...expanded, [feature]: !expanded[feature] };
	}

	function formatCost(v: number): string {
		if (v === 0) return '0';
		if (v < 0.01) return v.toFixed(4);
		return v.toFixed(3);
	}

	const featureColors: Record<string, string> = {
		Pulse: 'bg-blue-500',
		Omni: 'bg-emerald-500',
		Chat: 'bg-cyan-500',
		Coherence: 'bg-violet-500',
		Briefing: 'bg-amber-500',
		Egress: 'bg-red-500',
		System: 'bg-surface-500',
		Other: 'bg-pink-500'
	};

	const stepBarColor = 'bg-surface-400';
</script>

<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
	<h3 class="mb-4 text-xs font-semibold uppercase tracking-wider text-surface-400">
		LLM Cost by Feature ($)
	</h3>

	{#if features.length === 0}
		<p class="text-sm text-surface-500">No data</p>
	{:else}
		<div class="space-y-1">
			{#each features as feature}
				{@const pct = (feature.cost / maxValue) * 100}
				{@const isExpanded = expanded[feature.name] ?? false}
				{@const hasSteps = feature.steps.length > 1}

				<!-- Feature row -->
				<button
					type="button"
					class="group relative flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors {hasSteps ? 'cursor-pointer hover:bg-surface-700/50' : 'cursor-default'}"
					onclick={() => hasSteps && toggle(feature.name)}
				>
					<!-- Expand indicator -->
					<span class="w-3 text-[10px] text-surface-500">
						{#if hasSteps}
							<span class="inline-block transition-transform {isExpanded ? 'rotate-90' : ''}">&#9654;</span>
						{/if}
					</span>
					<span class="w-[35%] min-w-0 truncate text-xs font-medium text-surface-200">{feature.name}</span>
					<div class="relative flex-1">
						<div class="h-2.5 overflow-hidden rounded-full bg-surface-700">
							<div
								class="h-full rounded-full transition-all duration-300 {featureColors[feature.name] ?? 'bg-pink-500'}"
								style="width: {pct}%"
							></div>
						</div>
					</div>
					<span class="w-14 text-right text-xs tabular-nums text-surface-300">{formatCost(feature.cost)}</span>
				</button>

				<!-- Expanded step rows -->
				{#if isExpanded && hasSteps}
					{@const stepMax = Math.max(...feature.steps.map((s) => s.cost)) || 1}
					<div class="ml-6 space-y-0.5 border-l border-surface-700 pl-3">
						{#each feature.steps as step}
							{@const stepPct = (step.cost / stepMax) * 100}
							<div class="flex items-center gap-3 py-0.5">
								<span class="w-[35%] min-w-0 truncate text-[11px] text-surface-400">{step.label}</span>
								<div class="flex-1">
									<div class="h-1.5 overflow-hidden rounded-full bg-surface-700">
										<div
											class="h-full rounded-full {stepBarColor}"
											style="width: {stepPct}%"
										></div>
									</div>
								</div>
								<span class="w-14 text-right text-[11px] tabular-nums text-surface-500">{formatCost(step.cost)}</span>
							</div>
						{/each}
					</div>
				{/if}
			{/each}
		</div>
		<div class="mt-3 text-xs text-surface-500">Total: {formatCost(total)}</div>
	{/if}
</div>
