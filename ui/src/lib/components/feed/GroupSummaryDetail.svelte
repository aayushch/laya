<script lang="ts">
	import type { GroupSummary, CardGroup } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import StatusDot from './StatusDot.svelte';

	let {
		summary,
		group,
		generating = false,
		onclose,
		onshowcards,
		ongotocard,
		ongenerate,
	}: {
		summary: GroupSummary | null;
		group: CardGroup;
		generating?: boolean;
		onclose: () => void;
		onshowcards?: () => void;
		ongotocard?: (cardId: string) => void;
		ongenerate?: (entityId: string) => void;
	} = $props();

	let regenerateError = $state<string | null>(null);

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH: 'bg-orange-500 text-orange-50',
		MEDIUM: 'bg-laya-coral/20 text-laya-coral',
		LOW: 'bg-laya-gold/25 text-laya-amber'
	};

	const priorityLabel: Record<string, string> = {
		CRITICAL: 'CRIT',
		HIGH: 'HIGH',
		MEDIUM: 'MED',
		LOW: 'LOW'
	};

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};

	const statusSummary = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const c of group.cards) {
			counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
		}
		return [...counts.entries()].map(([status, count]) => ({ status, count }));
	});

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const diff = Date.now() - new Date(utcStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	const platformName = $derived(
		group.platforms && group.platforms.length > 1
			? 'Multiple'
			: platformLabel[group.platform] ?? group.platform
	);

	async function regenerate() {
		regenerateError = null;
		ongenerate?.(group.entity_id);
		try {
			await engineApi.regenerateGroupSummary(group.entity_id);
		} catch (e) {
			regenerateError = 'Failed to regenerate summary';
		}
	}
</script>

<div class="flex h-full flex-col overflow-hidden rounded-xl border border-surface-700 bg-surface-800">
	<!-- Header bar -->
	<div class="flex items-center justify-between border-b border-surface-700 px-5 py-4">
		<div class="flex items-center gap-2">
			<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
				{priorityLabel[group.top_priority] ?? group.top_priority}
			</span>
			<span class="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] font-medium text-surface-400">
				{group.card_count} cards
			</span>
		</div>
		<button aria-label="Close" class="rounded p-1.5 text-surface-400 transition-colors hover:text-surface-100" onclick={onclose}>
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
			</svg>
		</button>
	</div>

	<!-- Scrollable content -->
	<div class="flex-1 overflow-y-auto px-5 py-4">
		<!-- Platform badge -->
		<div class="mb-3">
			<PlatformBadge platform={group.platform} />
		</div>

		<!-- Entity title -->
		<h2 class="mb-1 text-lg font-semibold text-surface-50">{group.entity_title}</h2>
		{#if group.entity_url}
			<a href={group.entity_url} target="_blank" rel="noopener noreferrer" class="mb-4 inline-block text-xs text-laya-orange hover:underline">
				View source
			</a>
		{/if}

		<!-- Card status bar -->
		<div class="mb-5 flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-900/50 px-3 py-2">
			{#each statusSummary as { status, count }}
				<span class="flex items-center gap-1 shrink-0">
					<StatusDot {status} />
					<span class="text-[11px] text-surface-400">{count} {status.replace('_', ' ')}</span>
				</span>
			{/each}
		</div>

		{#if summary}
			<!-- Headline -->
			<div class="mb-4 rounded-lg border border-laya-orange/15 bg-laya-orange/5 px-4 py-3">
				<p class="text-sm font-medium text-surface-100">{summary.headline}</p>
			</div>

			<!-- Summary narrative -->
			<div class="mb-5">
				<p class="text-laya-base leading-relaxed text-surface-300">{summary.summary}</p>
			</div>

			<!-- Key developments -->
			{#if summary.key_events && summary.key_events.length > 0}
				<div class="mb-5">
					<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Key Developments</h3>
					<ul class="space-y-1.5">
						{#each summary.key_events as event}
							<li class="flex items-start gap-2 text-laya-base text-surface-300">
								<span class="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-laya-orange/50"></span>
								{event}
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			<!-- Current status -->
			{#if summary.current_status}
				<div class="mb-5">
					<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Current Status</h3>
					<div class="flex items-start gap-2">
						<span class="mt-1 h-2 w-2 flex-shrink-0 rounded-full bg-green-400"></span>
						<p class="text-laya-base text-surface-200">{summary.current_status}</p>
					</div>
				</div>
			{/if}

			<!-- Pending actions -->
			{#if summary.pending_actions && summary.pending_actions.length > 0}
				<div class="mb-5">
					<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Needs Attention</h3>
					<ul class="space-y-1.5">
						{#each summary.pending_actions as action}
							<li class="flex items-start gap-2 text-laya-base text-surface-300">
								<span class="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-400"></span>
								{action}
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			<!-- Updated time -->
			{#if summary.updated_at}
				<p class="mb-5 text-[10px] text-surface-500">
					Summary updated {timeAgo(summary.updated_at)}
				</p>
			{/if}
		{:else}
			<!-- No summary yet -->
			<div class="mb-5 flex flex-col items-center gap-3 rounded-lg border border-dashed border-surface-600 bg-surface-900/30 px-4 py-8 text-center">
				<svg class="h-8 w-8 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
				</svg>
				<div>
					<p class="text-sm font-medium text-surface-300">No summary yet</p>
					<p class="mt-1 text-xs text-surface-500">A summary will be generated when new activity arrives for this entity.</p>
				</div>
				<button
					class="mt-2 rounded-lg border border-laya-orange/30 bg-laya-orange/10 px-4 py-2 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/20 disabled:opacity-50"
					disabled={generating}
					onclick={regenerate}
				>
					{generating ? 'Generating...' : 'Generate now'}
				</button>
			</div>
		{/if}

		{#if regenerateError}
			<p class="mb-3 text-xs text-red-400">{regenerateError}</p>
		{/if}
	</div>

	<!-- Footer actions -->
	<div class="flex flex-col gap-2 border-t border-surface-700 px-5 py-4">
		{#if onshowcards}
			<button
				class="flex items-center justify-center gap-2 rounded-lg border border-surface-600 bg-surface-800 px-4 py-2.5 text-sm font-medium text-laya-orange transition-colors hover:bg-surface-700"
				onclick={onshowcards}
			>
				Show all {group.card_count} cards
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
				</svg>
			</button>
		{/if}
		{#if summary}
			<button
				class="flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-xs text-surface-500 transition-colors hover:text-surface-300 disabled:opacity-50"
				disabled={generating}
				onclick={regenerate}
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
				</svg>
				{generating ? 'Regenerating...' : 'Regenerate summary'}
			</button>
		{/if}
	</div>
</div>
