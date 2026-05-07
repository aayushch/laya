<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	let {
		card,
		compact = false,
		isLast = false,
		expandAll = null
	}: {
		card: ActionCard;
		compact?: boolean;
		isLast?: boolean;
		expandAll?: boolean | null;
	} = $props();

	let expanded = $state(false);

	// React to expand all / collapse all signal
	$effect(() => {
		if (expandAll !== null && expandAll !== undefined) {
			expanded = expandAll;
		}
	});

	const platformColors: Record<string, string> = {
		jira: 'bg-blue-500',
		github: 'bg-purple-500',
		bitbucket: 'bg-sky-500',
		slack: 'bg-green-500',
		gmail: 'bg-red-500',
		calendar: 'bg-yellow-500'
	};

	const platformTextColors: Record<string, string> = {
		jira: 'text-blue-400',
		github: 'text-purple-400',
		bitbucket: 'text-sky-400',
		slack: 'text-green-400',
		gmail: 'text-red-400',
		calendar: 'text-yellow-400'
	};

	const statusColors: Record<string, string> = {
		pending: 'text-yellow-400',
		ready: 'text-amber-400',
		agent_running: 'text-violet-400',
		awaiting_input: 'text-violet-400',
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

	let shortTime = $derived(
		card.created_at
			? new Date(card.created_at).toLocaleString(undefined, {
					month: 'short',
					day: 'numeric'
				})
			: ''
	);
</script>

{#if compact}
	<!-- Compact tree-leaf card using pl-5 + absolute line system -->
	<div class="relative pl-5">
		<!-- Vertical line: full height for non-last, partial for last (stops at dot center=10) -->
		{#if !isLast}
			<div class="absolute left-0 top-0 bottom-0 w-px bg-surface-700/30"></div>
		{:else}
			<div class="absolute left-0 top-0 w-px bg-surface-700/30" style="height: 10px"></div>
		{/if}

		<!-- Horizontal branch + dot: Y=10, dot center=10 → top=7 for 6px -->
		<div class="absolute left-0 top-[10px] w-[13px] h-px bg-surface-700/30"></div>
		<div class="absolute left-[10px] top-[7px] w-1.5 h-1.5 rounded-full {platformColors[platform] || 'bg-surface-500'}"></div>

		<button
			onclick={() => (expanded = !expanded)}
			class="w-full text-left flex items-center h-[20px] cursor-pointer
			       hover:bg-surface-800/40 rounded-sm transition-colors group"
		>
			<!-- Card header -->
			<span class="text-laya-secondary text-surface-200 leading-snug truncate flex-1 min-w-0">
				{card.header}
			</span>

			<!-- Priority badge -->
			<span class="px-1 py-0 rounded text-laya-micro font-medium shrink-0 ml-1.5 {priorityColors[card.priority] || ''}">
				{card.priority[0]}
			</span>

			<!-- Time -->
			<span class="text-laya-micro text-surface-600 shrink-0 tabular-nums ml-1">
				{shortTime}
			</span>
		</button>

		<!-- Expanded detail -->
		{#if expanded}
			<div class="pl-2 border-l border-surface-700/30 ml-1 mb-1">
				<p class="text-laya-secondary text-surface-400 leading-relaxed py-0.5">
					{card.summary}
				</p>

				{#if card.actor_name}
					<p class="text-laya-micro text-surface-500">
						{card.actor_name}
					</p>
				{/if}

				{#if card.intelligence && card.intelligence.length > 0}
					<div class="mt-1">
						{#each card.intelligence as item}
							<p class="text-laya-micro text-surface-400 flex gap-1">
								<span class="text-laya-orange shrink-0">-</span>
								<span>{item}</span>
							</p>
						{/each}
					</div>
				{/if}

				{#if card.staged_output?.content}
					<div class="mt-1 text-laya-micro text-surface-400 prose prose-invert prose-xs max-w-none">
						{@html DOMPurify.sanitize(marked(card.staged_output.content.slice(0, 300)) as string)}
					</div>
				{/if}

				{#if card.suggested_actions && card.suggested_actions.length > 0}
					<div class="flex flex-wrap gap-1 mt-1">
						{#each card.suggested_actions as action}
							<span class="px-1.5 py-0 rounded text-laya-micro bg-surface-700/60 text-surface-400">
								{action.label}
							</span>
						{/each}
					</div>
				{/if}

				{#if card.source_url}
					<a
						href={card.source_url}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-0.5 text-laya-micro text-laya-orange hover:text-laya-gold transition-colors mt-1"
						onclick={(e) => e.stopPropagation()}
					>
						open in {platform || 'source'}
						<svg class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-4.5-6H18m0 0v4.5m0-4.5L10.5 13.5" />
						</svg>
					</a>
				{/if}
			</div>
		{/if}
	</div>
{:else}
	<!-- Original full card view -->
	<div class="group relative">
		<div class="absolute left-0 top-3 w-3 h-3 rounded-full border-2 border-surface-700 {platformColors[platform] || 'bg-surface-500'} z-10"></div>

		<button
			class="ml-6 w-full text-left rounded-lg border border-surface-700/60 bg-surface-800/60
			       hover:border-surface-600 hover:bg-surface-800 transition-colors cursor-pointer"
			onclick={() => (expanded = !expanded)}
		>
			<div class="p-3">
				<div class="flex items-center gap-2 mb-1">
					<span class="text-laya-secondary font-mono uppercase tracking-wider {statusColors[card.status] || 'text-surface-400'}">
						{platform || '?'}
					</span>
					{#if card.source_ref}
						<span class="text-laya-secondary text-surface-500">{card.source_ref}</span>
					{/if}
					<span class="ml-auto text-laya-secondary text-surface-500">{timeStr}</span>
					<span class="px-1.5 py-0.5 rounded text-laya-micro font-medium {priorityColors[card.priority] || ''}">
						{card.priority}
					</span>
				</div>

				<h4 class="text-laya-base font-medium text-surface-100 leading-snug">
					{card.header}
				</h4>

				<p class="text-laya-secondary text-surface-400 mt-1 {expanded ? '' : 'line-clamp-2'}">
					{card.summary}
				</p>

				{#if card.actor_name}
					<p class="text-laya-secondary text-surface-500 mt-1">
						{card.actor_name}{card.actor_email ? ` (${card.actor_email})` : ''}
					</p>
				{/if}
			</div>

			{#if expanded}
				<div class="border-t border-surface-700/50 p-3 space-y-3">
					{#if card.intelligence && card.intelligence.length > 0}
						<div>
							<h5 class="text-laya-secondary font-medium text-surface-400 uppercase tracking-wider mb-1">Intelligence</h5>
							<ul class="space-y-0.5">
								{#each card.intelligence as item}
									<li class="text-laya-secondary text-surface-300 flex gap-1.5">
										<span class="text-laya-orange shrink-0">-</span>
										<span>{item}</span>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if card.staged_output?.content}
						<div>
							<h5 class="text-laya-secondary font-medium text-surface-400 uppercase tracking-wider mb-1">
								{card.staged_output.type.replace(/_/g, ' ')}
							</h5>
							<div class="text-laya-secondary text-surface-300 prose prose-invert prose-xs max-w-none">
								{@html DOMPurify.sanitize(marked(card.staged_output.content.slice(0, 500)) as string)}
							</div>
						</div>
					{/if}

					{#if card.suggested_actions && card.suggested_actions.length > 0}
						<div class="flex flex-wrap gap-1.5">
							{#each card.suggested_actions as action}
								<span class="px-2 py-0.5 rounded text-laya-secondary bg-surface-700 text-surface-300">
									{action.label}
								</span>
							{/each}
						</div>
					{/if}

					{#if card.source_url}
						<a
							href={card.source_url}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1 text-laya-secondary text-laya-orange hover:text-laya-gold transition-colors"
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
{/if}
