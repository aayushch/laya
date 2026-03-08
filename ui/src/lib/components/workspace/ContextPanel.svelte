<script lang="ts">
	import type { ActionCard, WorkspaceEvent, WorkspaceSession } from '$lib/api/types';

	let {
		card,
		session,
		events,
		context
	}: {
		card: ActionCard;
		session: WorkspaceSession | null;
		events: WorkspaceEvent[];
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

	const isTerminal = $derived(
		session ? ['completed', 'failed', 'cancelled'].includes(session.status) : false
	);

	const toolStats = $derived.by(() => {
		const counts: Record<string, number> = {};
		for (const ev of events) {
			if (ev.event_type === 'file_read') counts['Files read'] = (counts['Files read'] ?? 0) + 1;
			else if (ev.event_type === 'file_write') counts['Files written'] = (counts['Files written'] ?? 0) + 1;
			else if (ev.event_type === 'tool_call') {
				const tool = (ev.content.tool as string) ?? 'Other';
				if (tool === 'Bash') counts['Commands run'] = (counts['Commands run'] ?? 0) + 1;
				else if (tool === 'Grep' || tool === 'Glob') counts['Searches'] = (counts['Searches'] ?? 0) + 1;
				else counts['Other tools'] = (counts['Other tools'] ?? 0) + 1;
			}
		}
		return counts;
	});

	const sessionDuration = $derived.by(() => {
		if (!session?.started_at) return null;
		const end = session.completed_at ?? session.updated_at;
		if (!end) return null;
		const ms = new Date(end).getTime() - new Date(session.started_at).getTime();
		if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
		if (ms < 3600_000) return `${Math.round(ms / 60_000)}m`;
		return `${Math.round(ms / 3600_000 * 10) / 10}h`;
	});

	const relatedEntities = $derived(
		((context.related_entities as Array<{ entity_type: string; value: string } | string>) ?? []).map(
			(e) => (typeof e === 'string' ? e : e.value)
		)
	);
	const researchPlan = $derived((context.research_plan as string[]) ?? []);
</script>

<div class="flex h-full w-80 flex-col border-l border-surface-700 bg-surface-850">
	<div class="flex h-11 shrink-0 items-center border-b border-surface-700 px-4">
		<h2 class="text-xs font-semibold uppercase tracking-wider text-surface-400">Context</h2>
	</div>

	<div class="flex-1 overflow-y-auto space-y-4 p-4">
		<!-- Session outcome -->
		{#if session && isTerminal}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Session Outcome</h3>
				<div class="space-y-1.5 text-xs">
					<div class="flex justify-between">
						<span class="text-surface-400">Status</span>
						<span class={statusColors[session.status] ?? 'text-surface-300'}>{session.status}</span>
					</div>
					{#if sessionDuration}
						<div class="flex justify-between">
							<span class="text-surface-400">Duration</span>
							<span class="text-surface-200">{sessionDuration}</span>
						</div>
					{/if}
					{#if session.error_message}
						<div class="mt-1.5 rounded bg-red-900/20 px-2 py-1.5 text-[11px] text-red-300">
							{session.error_message}
						</div>
					{/if}
				</div>
				{#if Object.keys(toolStats).length > 0}
					<div class="mt-2.5 border-t border-surface-700/50 pt-2">
						<h4 class="mb-1.5 text-[10px] font-medium text-surface-500">Tool Usage</h4>
						<div class="space-y-1">
							{#each Object.entries(toolStats) as [label, count]}
								<div class="flex justify-between text-[11px]">
									<span class="text-surface-400">{label}</span>
									<span class="text-surface-300">{count}</span>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		{/if}

		<!-- Working Directory -->
		{#if session?.repo_path}
			<div class="rounded-lg border border-surface-700 bg-surface-800 p-3">
				<h3 class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Working Directory</h3>
				<p class="break-all font-mono text-[11px] text-surface-200">{session.repo_path}</p>
			</div>
		{/if}

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
					{card.staged_output.type === 'code_fix' ? 'Code Fix' : card.staged_output.type === 'draft_reply' ? 'Draft Reply' : card.staged_output.type === 'briefing' ? 'Briefing' : card.staged_output.type === 'agent_result' ? 'Agent Result' : card.staged_output.type === 'agent_plan' ? 'Implementation Plan' : 'Output'}
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
						<span class="min-w-0 max-w-full truncate rounded bg-surface-700 px-2 py-0.5 text-[11px] text-surface-300" title={entity}>{entity}</span>
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
