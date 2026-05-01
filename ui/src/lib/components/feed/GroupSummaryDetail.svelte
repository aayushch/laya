<script lang="ts">
	import type { GroupSummary, CardGroup } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
	import { chatOpen, chatCardContext, chatCardIds, chatListOpen } from '$lib/stores/chat';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import StatusDot from './StatusDot.svelte';
	import { glassTheme } from '$lib/stores/glassTheme';

	import type { ActionCard } from '$lib/api/types';

	let {
		summary,
		group,
		generating = false,
		onclose,
		ondismiss,
		onshowcards,
		ongotocard,
		ongotogroup,
		ongenerate,
		onshowrelated,
		onrunagent,
	}: {
		summary: GroupSummary | null;
		group: CardGroup;
		generating?: boolean;
		onclose: () => void;
		ondismiss?: () => void;
		onshowcards?: () => void;
		ongotocard?: (cardId: string) => void;
		ongotogroup?: (entityId: string) => void;
		ongenerate?: (entityId: string) => void;
		onshowrelated?: (card: ActionCard) => void;
		onrunagent?: (entityId: string) => void;
	} = $props();

	let regenerateError = $state<string | null>(null);
	let relatedCount = $state<number | null>(null);
	let overflowOpen = $state(false);
	let overflowBtnEl: HTMLElement | undefined = $state();

	const hasWorkspace = $derived(group.cards.some(c => c.has_workspace));
	const workspaceCardId = $derived(group.cards.find(c => c.has_workspace)?.card_id ?? group.cards[0]?.card_id);

	const footerActions = $derived.by(() => {
		const actions: string[] = [];
		if (onshowcards) actions.push('showcards');
		if (onshowrelated && relatedCount && relatedCount > 0) actions.push('related');
		if (onrunagent) {
			if (hasWorkspace) {
				actions.push('workspace');
			} else {
				actions.push('runagent');
			}
		}
		if (summary) actions.push('regenerate');
		return actions;
	});
	const inlineActions = $derived(footerActions.slice(0, 3));
	const overflowActions = $derived(footerActions.slice(3));

	$effect(() => {
		const firstCard = group.cards[0];
		relatedCount = null;
		if (!onshowrelated || !firstCard) return;
		engineApi.getRelatedCards(firstCard.card_id).then((data) => {
			if (group.cards[0]?.card_id === firstCard.card_id) {
				relatedCount = data.total_related_cards;
			}
		}).catch(() => {});
	});

	function chatAboutGroup() {
		const lines = [
			`The user is viewing a group of ${group.card_count} related cards.`,
			``,
			`Entity: ${group.entity_id}`,
			`Title: ${group.entity_title}`,
			`Platform: ${group.platform} | Priority: ${group.top_priority} | Cards: ${group.card_count}`,
		];
		if (summary) {
			lines.push(``, `Headline: ${summary.headline}`);
			lines.push(`Summary: ${summary.summary}`);
			if (summary.key_events?.length) {
				lines.push(``, `Key Events:`);
				summary.key_events.forEach((e) => lines.push(`- ${e}`));
			}
			if (summary.current_status) {
				lines.push(``, `Current Status: ${summary.current_status}`);
			}
			if (summary.pending_actions?.length) {
				lines.push(``, `Pending Actions:`);
				summary.pending_actions.forEach((a) => lines.push(`- ${a}`));
			}
		}
		for (const c of group.cards) {
			const cardLines = [
				``,
				`--- Card: ${c.card_id} ---`,
				`Title: ${c.header}`,
				`Summary: ${c.summary}`,
				`Priority: ${c.priority} | Status: ${c.status} | Persona: ${c.persona} | Category: ${c.category}`,
			];
			if (c.intelligence?.length) {
				cardLines.push('Intelligence:');
				c.intelligence.forEach((p) => cardLines.push(`- ${p}`));
			}
			lines.push(...cardLines);
		}
		const groupCardIds = group.cards.map((c) => c.card_id);
		chatCardContext.set(lines.join('\n'));
		chatCardIds.set(groupCardIds);
		chatListOpen.set(false);
		chatOpen.set(true);
	}

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

	const subjectId = $derived(group.entity_id?.includes(':') ? group.entity_id.split(':').pop() : group.entity_id);

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

<div class="flex h-full flex-col overflow-hidden rounded-xl border {$glassTheme ? 'glass-card border-surface-700/40 bg-surface-900/40' : 'border-surface-700 bg-surface-800'}">
	<!-- Header bar -->
	<div class="flex items-center justify-between border-b px-5 py-4 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
		<div class="flex items-center gap-2">
			<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
				{priorityLabel[group.top_priority] ?? group.top_priority}
			</span>
			<span class="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] font-medium text-surface-400">
				{group.card_count} cards
			</span>
		</div>
		<div class="flex items-center gap-1">
			{#if ongotogroup}
				<div class="group/act relative">
					<button
						onclick={() => ongotogroup?.(group.entity_id)}
						class="rounded p-1.5 text-surface-500 transition-colors hover:text-laya-orange"
						aria-label="Go to group"
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5" />
						</svg>
					</button>
					<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">Go to group</span>
				</div>
			{/if}
			<div class="group/act relative">
				<button
					onclick={chatAboutGroup}
					aria-label="Chat about this group"
					class="rounded p-1.5 text-surface-500 transition-colors hover:text-laya-orange"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">Chat about group</span>
			</div>
			<button aria-label="Close" class="rounded p-1.5 text-surface-400 transition-colors hover:text-surface-100" onclick={() => ondismiss ? ondismiss() : onclose()}>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Scrollable content -->
	<div class="flex-1 overflow-y-auto px-5 py-4">
		<!-- Platform + source reference -->
		<div class="mb-3 flex items-center gap-1.5 min-w-0">
			<PlatformBadge platform={group.platform} />
			{#if subjectId}
				{#if group.entity_url}
					<a
						href={group.entity_url}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-xs font-medium text-laya-orange hover:text-laya-peach transition-colors min-w-0 truncate"
					>
						<span class="truncate">{subjectId}</span>
						<svg class="h-2.5 w-2.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
						</svg>
					</a>
				{:else}
					<span class="text-xs font-medium text-surface-400 truncate">{subjectId}</span>
				{/if}
			{/if}
		</div>

		<!-- Entity title -->
		<h2 class="mb-2 text-lg font-semibold text-surface-50">{group.entity_title}</h2>

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

	<!-- Footer -->
	<div class="border-t px-5 py-2 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
		<div class="flex items-center justify-end gap-1">
			{#each inlineActions as key (key)}
				{@render footerButton(key)}
			{/each}
			{#if overflowActions.length > 0}
				<div class="relative">
					<button
						bind:this={overflowBtnEl}
						class="flex items-center justify-center rounded-md px-1.5 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-surface-200"
						onclick={() => (overflowOpen = !overflowOpen)}
						aria-label="More actions"
					>
						<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
							<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
						</svg>
					</button>
					{#if overflowOpen}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="absolute bottom-full right-0 mb-1 min-w-[140px] rounded-lg border p-1 shadow-xl z-20 {$glassTheme ? 'glass-card border-surface-700/40 bg-surface-900/80 backdrop-blur-xl' : 'border-surface-600 bg-surface-800'}"
							onmouseleave={() => (overflowOpen = false)}
						>
							{#each overflowActions as key (key)}
								{@render overflowItem(key)}
							{/each}
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
</div>

{#snippet footerButton(key: string)}
	{#if key === 'showcards'}
		<button
			class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-laya-orange"
			onclick={onshowcards}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
			</svg>
			Show all ({group.card_count})
		</button>
	{:else if key === 'related'}
		<button
			class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-laya-orange"
			onclick={() => onshowrelated?.(group.cards[0])}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="5" cy="12" r="2" stroke-width="2" /><circle cx="19" cy="6" r="2" stroke-width="2" /><circle cx="19" cy="18" r="2" stroke-width="2" /><path stroke-linecap="round" stroke-width="2" d="M7 11l10-4M7 13l10 4" /></svg>
			Related ({relatedCount})
		</button>
	{:else if key === 'runagent'}
		<button
			class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-cyan-400"
			onclick={() => onrunagent?.(group.entity_id)}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
			Run Agent
		</button>
	{:else if key === 'workspace'}
		<button
			class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-violet-400/80 transition-colors hover:bg-violet-500/10 hover:text-violet-400"
			onclick={() => goto(`/workspace/${workspaceCardId}`)}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
			Workspace
		</button>
	{:else if key === 'regenerate'}
		<button
			class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-surface-200 disabled:opacity-50"
			disabled={generating}
			onclick={regenerate}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
			</svg>
			{generating ? '...' : 'Regenerate'}
		</button>
	{/if}
{/snippet}

{#snippet overflowItem(key: string)}
	{@const hoverBg = $glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700/50'}
	{#if key === 'showcards'}
		<button
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-surface-300 transition-colors {hoverBg} hover:text-laya-orange"
			onclick={() => { overflowOpen = false; onshowcards?.(); }}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
			</svg>
			Show all ({group.card_count})
		</button>
	{:else if key === 'related'}
		<button
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-surface-300 transition-colors {hoverBg} hover:text-laya-orange"
			onclick={() => { overflowOpen = false; onshowrelated?.(group.cards[0]); }}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="5" cy="12" r="2" stroke-width="2" /><circle cx="19" cy="6" r="2" stroke-width="2" /><circle cx="19" cy="18" r="2" stroke-width="2" /><path stroke-linecap="round" stroke-width="2" d="M7 11l10-4M7 13l10 4" /></svg>
			Related ({relatedCount})
		</button>
	{:else if key === 'runagent'}
		<button
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-surface-300 transition-colors {hoverBg} hover:text-cyan-400"
			onclick={() => { overflowOpen = false; onrunagent?.(group.entity_id); }}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
			Run Agent
		</button>
	{:else if key === 'workspace'}
		<button
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-violet-400/80 transition-colors {hoverBg} hover:text-violet-400"
			onclick={() => { overflowOpen = false; goto(`/workspace/${workspaceCardId}`); }}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
			Workspace
		</button>
	{:else if key === 'regenerate'}
		<button
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-surface-300 transition-colors {hoverBg} hover:text-surface-200 disabled:opacity-50"
			disabled={generating}
			onclick={() => { overflowOpen = false; regenerate(); }}
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
			</svg>
			{generating ? '...' : 'Regenerate'}
		</button>
	{/if}
{/snippet}
