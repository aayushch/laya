<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import ActionCardComponent from './ActionCard.svelte';

	let {
		group,
		onselect,
		ondelete,
		selectedCardId = '',
		hasSelection = false
	}: { group: CardGroup; onselect: (card: ActionCard) => void; ondelete?: (cardId: string) => void; selectedCardId?: string; hasSelection?: boolean } = $props();

	let expanded = $state(false);
	let dismissingAll = $state(false);

	const topCard = $derived(group.cards.find((c) => c.status === 'pending') ?? group.cards[0]);
	const extraCount = $derived(group.card_count - 1);
	// Show up to 2 ghost strips behind the front card
	const ghostCount = $derived(Math.min(extraCount, 2));

	// Status summary for collapsed view
	const statusSummary = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const c of group.cards) {
			counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
		}
		return [...counts.entries()].map(([status, count]) => ({ status, count }));
	});

	const statusSummaryDot: Record<string, string> = {
		pending: 'bg-yellow-400', approved: 'bg-green-400', executing: 'bg-blue-400',
		completed: 'bg-green-500', failed: 'bg-red-500', dismissed: 'bg-surface-500',
		archived: 'bg-surface-600', agent_running: 'bg-violet-400',
		awaiting_input: 'bg-yellow-400', staged: 'bg-emerald-400'
	};

	const statusSummaryLabel: Record<string, string> = {
		pending: 'Pending', approved: 'Approved', dismissed: 'Dismissed',
		archived: 'Archived', completed: 'Completed', failed: 'Failed',
		executing: 'Executing', agent_running: 'Running',
		awaiting_input: 'Needs Input', staged: 'Staged'
	};

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH:     'bg-orange-500 text-orange-50',
		MEDIUM:   'bg-blue-600 text-blue-50',
		LOW:      'bg-laya-gold/25 text-laya-amber'
	};

	const personaColors: Record<string, string> = {
		ENGINEER: 'text-violet-400',
		COMMS:    'text-emerald-400',
		OPS:      'text-amber-400'
	};

	const statusDot: Record<string, string> = {
		pending:        'bg-yellow-400',
		approved:       'bg-green-400',
		dismissed:      'bg-surface-500',
		archived:       'bg-surface-600',
		completed:      'bg-green-500',
		failed:         'bg-red-500',
		executing:      'bg-blue-400 animate-pulse',
		agent_running:  'bg-violet-400 animate-pulse',
		awaiting_input: 'bg-yellow-400 animate-pulse',
		staged:         'bg-emerald-400'
	};

	const statusLabel: Record<string, string> = {
		pending: 'Pending', approved: 'Approved', dismissed: 'Dismissed',
		archived: 'Archived', completed: 'Completed', failed: 'Failed',
		executing: 'Executing', agent_running: 'Agent Running',
		awaiting_input: 'Input Needed', staged: 'Staged'
	};

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
		// Backend stores UTC timestamps; ensure JS parses them as UTC
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const diff = Date.now() - new Date(utcStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	const isGroupSelected = $derived(group.cards.some((c) => c.card_id === selectedCardId));
	const isDimmed = $derived(hasSelection && !isGroupSelected);

	function toggle() {
		expanded = !expanded;
	}

	async function dismissAll(e: Event) {
		e.stopPropagation();
		dismissingAll = true;
		try {
			await engineApi.dismissGroup(group.entity_id);
			for (const card of group.cards) {
				if (!['completed', 'dismissed', 'failed'].includes(card.status)) {
					card.status = 'dismissed';
				}
			}
		} finally {
			dismissingAll = false;
		}
	}
</script>

{#if !expanded}
	<!--
	  ── COLLAPSED: Stack view ──────────────────────────────────────────────────
	  The main front card sits in normal flow. Ghost strips are absolutely
	  positioned inside the container's 12px padding-bottom zone, peeking
	  out below the front card to give the "deck of cards" illusion.
	-->
	<div class="relative transition-opacity {isDimmed ? 'opacity-45 hover:opacity-70' : ''}" style="padding-bottom: {ghostCount > 0 ? 12 : 0}px">

		<!-- Ghost strip 2 — furthest back, most inset, sits at container bottom -->
		{#if ghostCount >= 2}
			<div class="absolute bottom-0 rounded-b-xl border-x border-b border-surface-700 bg-surface-950"
				style="left: 16px; right: 16px; height: 8px; z-index: 1;"></div>
		{/if}

		<!-- Ghost strip 1 — one step back, 5px above container bottom -->
		{#if ghostCount >= 1}
			<div class="absolute rounded-b-xl border-x border-b border-surface-600 bg-surface-900"
				style="bottom: 5px; left: 8px; right: 8px; height: 8px; z-index: 2;"></div>
		{/if}

		<!-- Front card — sits above all ghosts, matches ActionCard fixed height -->
		<button
			class="relative flex h-[200px] w-full flex-col rounded-xl border border-surface-600 bg-surface-900 px-4 pb-2 pt-3 text-left shadow-lg transition hover:border-laya-orange/30"
			style="z-index: 3;"
			onclick={toggle}
		>
			<!-- Top row: source · priority · count -->
			<div class="mb-2 flex items-center justify-between">
				<span class="text-[10px] font-semibold uppercase tracking-widest text-surface-500">
					{platformLabel[group.platform] ?? group.platform}
				</span>
				<div class="flex items-center gap-1.5">
					<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
						{group.top_priority}
					</span>
					<span class="rounded-full border border-surface-600 bg-surface-700 px-2 py-0.5 text-[10px] font-semibold text-surface-300">
						+{extraCount}
					</span>
				</div>
			</div>

			<!-- Entity title (2-line clamp) -->
			<h3 class="mb-1.5 line-clamp-2 text-sm font-semibold leading-snug text-surface-50" title={group.entity_title}>
				{group.entity_title}
			</h3>

			<!-- Top card preview summary (2-line clamp) -->
			<p class="line-clamp-2 text-xs leading-relaxed text-surface-400" title={topCard.summary}>
				{topCard.summary}
			</p>

			<!-- Status summary — fills remaining space -->
			<div class="mt-2 flex flex-1 items-end">
				<div class="flex flex-wrap gap-x-3 gap-y-0.5">
					{#each statusSummary as { status, count }}
						<span class="flex items-center gap-1">
							<span class="h-1.5 w-1.5 rounded-full {statusSummaryDot[status] ?? 'bg-surface-500'}"></span>
							<span class="text-[10px] text-surface-400">{count} {statusSummaryLabel[status] ?? status}</span>
						</span>
					{/each}
				</div>
				<span class="ml-auto shrink-0 text-[10px] text-surface-600">
					{group.card_count} cards
				</span>
			</div>
		</button>
	</div>

{:else}
	<!--
	  ── EXPANDED: Current list view ────────────────────────────────────────────
	-->
	<div class="overflow-hidden rounded-xl border border-surface-600 bg-surface-900 shadow-lg">
		<!-- Header: platform · entity title · controls -->
		<div
			role="button"
			tabindex="0"
			class="flex w-full cursor-pointer flex-col gap-1.5 px-4 py-3 text-left transition-colors hover:bg-surface-800/50"
			onclick={toggle}
			onkeydown={(e) => e.key === 'Enter' && toggle()}
		>
			<div class="flex items-center gap-2">
				<span class="text-[10px] font-semibold uppercase tracking-widest text-surface-500">
					{platformLabel[group.platform] ?? group.platform}
				</span>
				<div class="ml-auto flex items-center gap-2">
					<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
						{group.top_priority}
					</span>
					{#if group.has_pending}
						<button
							onclick={dismissAll}
							disabled={dismissingAll}
							class="text-[10px] text-surface-500 transition-colors hover:text-red-400 disabled:opacity-50"
						>
							{dismissingAll ? 'Dismissing…' : 'Dismiss all'}
						</button>
					{/if}
					<!-- Collapse chevron (pointing up = expanded) -->
					<svg class="h-3.5 w-3.5 shrink-0 rotate-180 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</div>
			</div>
			<span class="line-clamp-2 text-sm font-semibold leading-snug text-surface-100">
				{group.entity_title}
			</span>
		</div>

		<!-- Card list -->
		<div class="space-y-2 px-3 pb-3 pt-2">
			{#each group.cards as card (card.card_id)}
				<ActionCardComponent {card} onselect={onselect} {ondelete} {selectedCardId} {hasSelection} />
			{/each}
		</div>
	</div>
{/if}
