<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { slide } from 'svelte/transition';
	import ListRow from './ListRow.svelte';

	let {
		group,
		onselect,
		ondelete,
		selectedCardId = '',
		scrollToCardId = null,
		bulkSelectedIds,
		onbulktoggle,
		onbulktogglegroup
	}: {
		group: CardGroup;
		onselect: (card: ActionCard) => void;
		ondelete?: (cardId: string) => void;
		selectedCardId?: string;
		scrollToCardId?: string | null;
		bulkSelectedIds?: Set<string>;
		onbulktoggle?: (cardId: string, event: MouseEvent) => void;
		onbulktogglegroup?: (cardIds: string[], selected: boolean) => void;
	} = $props();

	let expanded = $state(false);
	let groupMenuOpen = $state(false);
	let bulkActionRunning = $state(false);

	// Auto-expand when a card in this group is targeted for scroll
	$effect(() => {
		if (scrollToCardId && !expanded && group.cards.some((c) => c.card_id === scrollToCardId)) {
			expanded = true;
		}
	});

	// Close group menu on outside click
	$effect(() => {
		if (!groupMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!target.closest('.group-menu')) groupMenuOpen = false;
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};

	const topCard = $derived(group.cards[0]);

	// Status priority for group color: highest-priority status wins
	const statusPriority: string[] = [
		'awaiting_input', 'failed', 'agent_running', 'requires_approval',
		'pending', 'ready', 'done', 'dismissed', 'archived'
	];

	const groupRowStyle: Record<string, string> = {
		pending:            'bg-amber-950/55  hover:bg-amber-950/70',
		ready:              'bg-amber-950/55  hover:bg-amber-950/70',
		requires_approval:  'bg-violet-950/55 hover:bg-violet-950/70',
		agent_running:      'bg-violet-950/55 hover:bg-violet-950/70',
		awaiting_input:     'bg-amber-950/55  hover:bg-amber-950/70',
		done:               'bg-emerald-950/50 hover:bg-emerald-950/65',
		failed:             'bg-rose-950/60   hover:bg-rose-950/75',
		dismissed:          'bg-surface-800/40 hover:bg-surface-800/60',
		archived:           'bg-surface-900/60 hover:bg-surface-900/80',
	};

	const dominantStatus = $derived.by(() => {
		const statuses = new Set(group.cards.map(c => c.status));
		for (const s of statusPriority) {
			if (statuses.has(s as ActionCard['status'])) return s;
		}
		return group.cards[0]?.status ?? 'pending';
	});

	const allArchived = $derived(group.cards.every(c => c.status === 'archived'));

	const groupBgStyle = $derived(
		allArchived
			? 'bg-surface-900/60 opacity-50 hover:opacity-80'
			: (groupRowStyle[dominantStatus] ?? 'hover:bg-surface-800/60')
	);

	const statusDisplayLabel: Record<string, string> = {
		pending: 'processing', ready: 'ready', requires_approval: 'needs approval',
		agent_running: 'running', awaiting_input: 'needs input', done: 'done',
		failed: 'failed', dismissed: 'dismissed', archived: 'archived'
	};

	const statusSummaryTooltip = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const c of group.cards) {
			counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
		}
		return [...counts.entries()]
			.map(([status, count]) => `${count} ${statusDisplayLabel[status] ?? status}`)
			.join(', ');
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

	// Group bulk selection state
	const groupCardIds = $derived(group.cards.map(c => c.card_id));
	const selectedInGroupCount = $derived(bulkSelectedIds ? groupCardIds.filter(id => bulkSelectedIds.has(id)).length : 0);
	const allGroupSelected = $derived(selectedInGroupCount === groupCardIds.length && groupCardIds.length > 0);
	const someGroupSelected = $derived(selectedInGroupCount > 0 && !allGroupSelected);

	let groupCheckboxEl: HTMLButtonElement | undefined = $state();

	function toggleGroupCheckbox(e: MouseEvent) {
		e.stopPropagation();
		if (allGroupSelected) {
			onbulktogglegroup?.(groupCardIds, false);
		} else {
			onbulktogglegroup?.(groupCardIds, true);
		}
	}

	function toggle() { expanded = !expanded; }
	function toggleGroupMenu(e: Event) { e.stopPropagation(); groupMenuOpen = !groupMenuOpen; }

	// Bulk actions
	const canApproveAll = $derived(group.cards.some((c) => c.status === 'requires_approval'));
	const canCompleteAll = $derived(group.cards.some((c) => c.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(c.status)));
	const canDismissAll = $derived(group.cards.some((c) => c.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(c.status)));
	const canArchiveAll = $derived(group.cards.some((c) => c.status !== 'archived'));
	const canReopenAll = $derived(group.cards.some((c) => ['dismissed', 'archived', 'done'].includes(c.status)));
	const canUnarchiveAll = $derived(group.cards.some((c) => c.status === 'archived'));
	const hasAnyAction = $derived(canApproveAll || canCompleteAll || canDismissAll || canArchiveAll || canReopenAll || canUnarchiveAll);

	async function bulkAction(action: 'approve' | 'complete' | 'dismiss' | 'archive' | 'reopen' | 'unarchive', e: Event) {
		e.stopPropagation();
		groupMenuOpen = false;
		bulkActionRunning = true;
		try {
			const promises: Promise<unknown>[] = [];
			for (const card of group.cards) {
				switch (action) {
					case 'approve':
						if (card.status === 'requires_approval') promises.push(engineApi.approveAgent(card.card_id).then(() => { card.status = 'agent_running'; }));
						break;
					case 'complete':
						if (card.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(card.status)) promises.push(engineApi.markCardDone(card.card_id).then(() => { card.status = 'done'; }));
						break;
					case 'dismiss':
						if (card.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(card.status)) promises.push(engineApi.dismissCard(card.card_id).then(() => { card.status = 'dismissed'; }));
						break;
					case 'archive':
						if (card.status !== 'archived') promises.push(engineApi.archiveCard(card.card_id).then(() => { card.status = 'archived'; }));
						break;
					case 'reopen':
						if (['dismissed', 'archived', 'done'].includes(card.status)) promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						break;
					case 'unarchive':
						if (card.status === 'archived') promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						break;
				}
			}
			await Promise.all(promises);
		} finally {
			bulkActionRunning = false;
		}
	}
</script>

<div>
	<!-- Group header: checkbox in gutter + bordered row -->
	<div class="flex items-center {onbulktoggle ? 'gap-1.5' : ''}">
		<!-- Bulk selection checkbox (group-level) — in the gutter -->
		{#if onbulktoggle}
			<div class="w-5 shrink-0 flex items-center justify-center">
				<button
					bind:this={groupCheckboxEl}
					class="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors
						{allGroupSelected
							? 'bg-laya-orange border-laya-orange'
							: someGroupSelected
								? 'bg-laya-orange/50 border-laya-orange'
								: 'border-surface-500 hover:border-surface-300 bg-transparent'}"
					onclick={toggleGroupCheckbox}
					aria-label="{allGroupSelected ? 'Deselect' : 'Select'} all cards in group"
				>
					{#if allGroupSelected}
						<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
						</svg>
					{:else if someGroupSelected}
						<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
							<path stroke-linecap="round" d="M6 12h12" />
						</svg>
					{/if}
				</button>
			</div>
		{/if}

		<div class="flex-1 rounded-lg border {expanded ? 'border-surface-600 bg-surface-900' : 'border-surface-700/40 ' + groupBgStyle} transition-colors">
			<!-- Group header row -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="group/grow flex items-center px-3 py-1.5 cursor-pointer transition-colors"
				onclick={toggle}
				onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}
				role="button"
				tabindex="0"
			>
				<!-- Expand/collapse chevron — same w-5 as card spacer -->
				<button aria-label="{expanded ? 'Collapse' : 'Expand'} group" class="w-5 shrink-0 flex items-center justify-center rounded text-surface-500 hover:text-surface-300">
					<svg class="h-3 w-3 transition-transform {expanded ? '' : '-rotate-90'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</button>

				<!-- Source — fixed width, matches ListRow -->
		<span class="w-[60px] shrink-0 text-[10px] font-semibold uppercase tracking-wider text-surface-500 truncate">
			{platformLabel[group.platform] ?? group.platform}
		</span>

		<!-- Subject (entity title) — skip actor column, use ml to match card's actor+gap -->
		<span class="min-w-0 flex-1 truncate text-xs font-medium text-surface-200 ml-2" title={group.entity_title}>
			{group.entity_title}
		</span>

		<!-- Card count — with status summary tooltip -->
		<div class="group/count relative shrink-0 ml-2">
			<span class="rounded-full border border-surface-600 bg-surface-700 px-2 py-0.5 text-[10px] font-semibold text-surface-300 cursor-default">
				{group.card_count}
			</span>
			<span class="pointer-events-none absolute top-full left-1/2 -translate-x-1/2 z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/count:opacity-100">
				{statusSummaryTooltip}
			</span>
		</div>

		<!-- Three-dot menu -->
		{#if hasAnyAction}
			<div class="group-menu relative shrink-0 ml-1">
				<button
					class="flex h-5 w-5 items-center justify-center rounded text-surface-500 hover:bg-surface-700 hover:text-surface-300 disabled:opacity-50 opacity-0 group-hover/grow:opacity-100 transition-opacity"
					onclick={toggleGroupMenu}
					disabled={bulkActionRunning}
					title="Group actions"
				>
					{#if bulkActionRunning}
						<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
						</svg>
					{:else}
						<svg class="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
							<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
						</svg>
					{/if}
				</button>
				{#if groupMenuOpen}
					<div class="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-surface-600 bg-surface-800 p-1 shadow-xl shadow-black/30" role="menu">
						{#if canApproveAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-violet-400" role="menuitem" onclick={(e) => bulkAction('approve', e)}>Approve All</button>
						{/if}
						{#if canCompleteAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-green-400" role="menuitem" onclick={(e) => bulkAction('complete', e)}>Complete All</button>
						{/if}
						{#if canDismissAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-red-400" role="menuitem" onclick={(e) => bulkAction('dismiss', e)}>Dismiss All</button>
						{/if}
						{#if canReopenAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('reopen', e)}>Reopen All</button>
						{/if}
						{#if canArchiveAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-surface-400" role="menuitem" onclick={(e) => bulkAction('archive', e)}>Archive All</button>
						{/if}
						{#if canUnarchiveAll}
							<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('unarchive', e)}>Unarchive All</button>
						{/if}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Space badge — fixed width, matches ListRow -->
		<span class="w-[72px] shrink-0 flex items-center gap-1 ml-1 truncate">
			{#if topCard?.space_name}
				<span class="inline-flex items-center gap-1 rounded border border-surface-700 bg-surface-800/60 px-1.5 py-0.5 text-[9px] text-surface-400 truncate">
					<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {topCard.space_color ?? '#F97316'}"></span>
					<span class="truncate">{topCard.space_name}</span>
				</span>
			{/if}
		</span>

		<!-- Time — fixed width, matches ListRow -->
		<span class="w-[52px] shrink-0 text-right text-[10px] text-surface-500 whitespace-nowrap">{timeAgo(group.latest_at)}</span>
	</div>

		</div>
	</div>

	<!-- Expanded: child card rows — rendered outside bordered container so checkboxes align in gutter -->
	{#if expanded}
		<div class="py-1" transition:slide={{ duration: 200 }}>
			{#each group.cards as card (card.card_id)}
				<ListRow {card} {onselect} {ondelete} {selectedCardId} indented={true} bulkSelected={bulkSelectedIds?.has(card.card_id) ?? false} {onbulktoggle} />
			{/each}
		</div>
	{/if}
</div>
