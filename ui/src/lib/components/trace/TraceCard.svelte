<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { marked } from 'marked';

	let {
		card
	}: {
		card: ActionCard;
	} = $props();

	let expanded = $state(false);

	const platformColors: Record<string, string> = {
		jira: 'bg-blue-500',
		github: 'bg-purple-500',
		bitbucket: 'bg-sky-500',
		slack: 'bg-green-500',
		gmail: 'bg-red-500',
		calendar: 'bg-yellow-500'
	};

	const statusColors: Record<string, string> = {
		pending: 'text-yellow-400',
		ready: 'text-amber-400',
		requires_approval: 'text-violet-400',
		agent_running: 'text-violet-400',
		awaiting_input: 'text-yellow-400',
		done: 'text-green-400',
		failed: 'text-red-400',
		dismissed: 'text-surface-500',
		archived: 'text-surface-600'
	};

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH: 'bg-orange-500 text-orange-50',
		MEDIUM: 'bg-laya-coral/20 text-laya-coral',
		LOW: 'bg-laya-gold/25 text-laya-amber'
	};

	let platform = $derived(
		card.entity_id && card.entity_id.includes(':') ? card.entity_id.split(':')[0] : ''
	);

	let timeStr = $derived(
		card.created_at
			? new Date(card.created_at).toLocaleString(undefined, {
					month: 'short',
					day: 'numeric',
					hour: '2-digit',
					minute: '2-digit'
				})
			: ''
	);
</script>

<div class="group relative">
	<!-- Timeline connector dot -->
	<div class="absolute left-0 top-3 w-3 h-3 rounded-full border-2 border-surface-700 {platformColors[platform] || 'bg-surface-500'} z-10"></div>

	<!-- Card content -->
	<button
		class="ml-6 w-full text-left rounded-lg border border-surface-700/60 bg-surface-800/60
		       hover:border-surface-600 hover:bg-surface-800 transition-colors cursor-pointer"
		onclick={() => (expanded = !expanded)}
	>
		<div class="p-3">
			<!-- Header row -->
			<div class="flex items-center gap-2 mb-1">
				<span class="text-xs font-mono uppercase tracking-wider {statusColors[card.status] || 'text-surface-400'}">
					{platform || '?'}
				</span>
				{#if card.source_ref}
					<span class="text-xs text-surface-500">{card.source_ref}</span>
				{/if}
				<span class="ml-auto text-xs text-surface-500">{timeStr}</span>
				<span class="px-1.5 py-0.5 rounded text-[10px] font-medium {priorityColors[card.priority] || ''}">
					{card.priority}
				</span>
			</div>

			<!-- Title -->
			<h4 class="text-sm font-medium text-surface-100 leading-snug">
				{card.header}
			</h4>

			<!-- Summary (truncated when collapsed) -->
			<p class="text-xs text-surface-400 mt-1 {expanded ? '' : 'line-clamp-2'}">
				{card.summary}
			</p>

			{#if card.actor_name}
				<p class="text-xs text-surface-500 mt-1">
					{card.actor_name}{card.actor_email ? ` (${card.actor_email})` : ''}
				</p>
			{/if}
		</div>

		<!-- Expanded content -->
		{#if expanded}
			<div class="border-t border-surface-700/50 p-3 space-y-3">
				<!-- Intelligence -->
				{#if card.intelligence && card.intelligence.length > 0}
					<div>
						<h5 class="text-xs font-medium text-surface-400 uppercase tracking-wider mb-1">Intelligence</h5>
						<ul class="space-y-0.5">
							{#each card.intelligence as item}
								<li class="text-xs text-surface-300 flex gap-1.5">
									<span class="text-laya-orange shrink-0">-</span>
									<span>{item}</span>
								</li>
							{/each}
						</ul>
					</div>
				{/if}

				<!-- Staged output -->
				{#if card.staged_output?.content}
					<div>
						<h5 class="text-xs font-medium text-surface-400 uppercase tracking-wider mb-1">
							{card.staged_output.type.replace(/_/g, ' ')}
						</h5>
						<div class="text-xs text-surface-300 prose prose-invert prose-xs max-w-none">
							{@html marked(card.staged_output.content.slice(0, 500))}
						</div>
					</div>
				{/if}

				<!-- Actions -->
				{#if card.suggested_actions && card.suggested_actions.length > 0}
					<div class="flex flex-wrap gap-1.5">
						{#each card.suggested_actions as action}
							<span class="px-2 py-0.5 rounded text-xs bg-surface-700 text-surface-300">
								{action.label}
							</span>
						{/each}
					</div>
				{/if}

				<!-- Source link -->
				{#if card.source_url}
					<a
						href={card.source_url}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-xs text-laya-orange hover:text-laya-gold transition-colors"
						onclick={(e) => e.stopPropagation()}
					>
						Open in {platform || 'source'}
						<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-4.5-6H18m0 0v4.5m0-4.5L10.5 13.5" />
						</svg>
					</a>
				{/if}
			</div>
		{/if}
	</button>
</div>
