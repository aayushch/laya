<script lang="ts">
	import type { ActionCard, SuggestedAction } from '$lib/api/types';

	let {
		card,
		context
	}: {
		card: ActionCard;
		context: Record<string, unknown>;
	} = $props();

	const priorityColors: Record<string, string> = {
		CRITICAL: 'text-red-400',
		HIGH: 'text-orange-400',
		MEDIUM: 'text-blue-400',
		LOW: 'text-surface-400'
	};

	const statusColors: Record<string, string> = {
		pending: 'text-yellow-400',
		approved: 'text-green-400',
		executing: 'text-blue-400',
		completed: 'text-green-500',
		failed: 'text-red-500',
		dismissed: 'text-surface-500',
		agent_running: 'text-violet-400',
		awaiting_input: 'text-yellow-400',
		staged: 'text-emerald-400'
	};

	const relatedEntities = $derived((context.related_entities as string[]) ?? []);
	const researchPlan = $derived((context.research_plan as string[]) ?? []);
</script>

<div class="flex h-full w-80 flex-col overflow-y-auto border-l border-surface-700 bg-surface-850">
	<div class="border-b border-surface-700 px-4 py-3">
		<h2 class="text-xs font-semibold uppercase tracking-wider text-surface-400">Context</h2>
	</div>

	<div class="space-y-4 p-4">
		<!-- Card metadata -->
		<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
			<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Card Info</h3>
			<div class="space-y-1.5 text-xs">
				<div class="flex justify-between">
					<span class="text-surface-400">Priority</span>
					<span class={priorityColors[card.priority] ?? 'text-surface-300'}>{card.priority}</span>
				</div>
				<div class="flex justify-between">
					<span class="text-surface-400">Persona</span>
					<span class="text-surface-200">{card.persona}</span>
				</div>
				<div class="flex justify-between">
					<span class="text-surface-400">Category</span>
					<span class="text-surface-200">{card.category}</span>
				</div>
				<div class="flex justify-between">
					<span class="text-surface-400">Status</span>
					<span class={statusColors[card.status] ?? 'text-surface-300'}>{card.status}</span>
				</div>
				{#if card.confidence}
					<div class="flex justify-between">
						<span class="text-surface-400">Confidence</span>
						<span class="text-surface-200">{Math.round(card.confidence * 100)}%</span>
					</div>
				{/if}
			</div>
		</div>

		<!-- Staged output -->
		{#if card.staged_output}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">
					{card.staged_output.type === 'code_fix' ? 'Code Fix' : card.staged_output.type === 'draft_reply' ? 'Draft Reply' : card.staged_output.type === 'briefing' ? 'Briefing' : 'Output'}
				</h3>
				{#if card.staged_output.type === 'code_fix'}
					<pre class="overflow-x-auto rounded bg-surface-900 p-2 text-[11px] text-surface-200 max-h-48">{card.staged_output.content}</pre>
				{:else}
					<p class="text-xs text-surface-200 whitespace-pre-wrap max-h-48 overflow-y-auto">{card.staged_output.content}</p>
				{/if}
			</div>
		{/if}

		<!-- Related entities -->
		{#if relatedEntities.length > 0}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Related Entities</h3>
				<div class="flex flex-wrap gap-1.5">
					{#each relatedEntities as entity}
						<span class="rounded bg-surface-700 px-2 py-0.5 text-[11px] text-surface-300">{entity}</span>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Research plan -->
		{#if researchPlan.length > 0}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Research Plan</h3>
				<ol class="space-y-1">
					{#each researchPlan as step, i}
						<li class="flex items-start gap-2 text-xs text-surface-300">
							<span class="flex-shrink-0 text-surface-500">{i + 1}.</span>
							{step}
						</li>
					{/each}
				</ol>
			</div>
		{/if}

		<!-- Intelligence -->
		{#if card.intelligence && card.intelligence.length > 0}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Intelligence</h3>
				<ul class="space-y-1">
					{#each card.intelligence as point}
						<li class="flex items-start gap-2 text-xs text-surface-300">
							<span class="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-surface-500"></span>
							{point}
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<!-- Suggested actions -->
		{#if card.suggested_actions && card.suggested_actions.length > 0}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Actions</h3>
				<div class="space-y-1.5">
					{#each card.suggested_actions as action}
						<div class="flex items-center justify-between text-xs">
							<span class="text-surface-200">{action.label}</span>
							<span class="text-[10px] text-surface-500">{action.target_platform}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</div>
