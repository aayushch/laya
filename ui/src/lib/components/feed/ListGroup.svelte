<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { slide } from 'svelte/transition';
	import { cardColors } from '$lib/stores/cardColors';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import ListRow from './ListRow.svelte';

	let {
		group,
		onselect,
		ondelete,
		onlink,
		selectedCardId = '',
		scrollToCardId = null,
		bulkSelectedIds,
		onbulktoggle,
		onbulktogglegroup,
		hasSelection = false,
		lastViewedCardId = ''
	}: {
		group: CardGroup;
		onselect: (card: ActionCard) => void;
		ondelete?: (cardId: string) => void;
		onlink?: (group: CardGroup) => void;
		selectedCardId?: string;
		scrollToCardId?: string | null;
		bulkSelectedIds?: Set<string>;
		onbulktoggle?: (cardId: string, event: MouseEvent) => void;
		onbulktogglegroup?: (cardIds: string[], selected: boolean) => void;
		hasSelection?: boolean;
		lastViewedCardId?: string;
	} = $props();

	let expanded = $state(false);
	let groupMenuOpen = $state(false);
	let menuEl: HTMLElement | undefined = $state();
	let bulkActionRunning = $state(false);
	let unlinking = $state(false);

	async function unlinkGroup(e: Event) {
		e.stopPropagation();
		groupMenuOpen = false;
		if (!group.context_id) return;
		unlinking = true;
		try {
			await engineApi.unlinkContextGroup(group.context_id);
		} finally {
			unlinking = false;
		}
	}

	// Extract subject ID from entity_id (e.g., "jira:ticket:FERR-1056" → "FERR-1056")
	// For linked groups, use the top card's entity_id instead of the context_id
	const effectiveEntityId = $derived(
		group.context_id && group.cards[0]?.entity_id
			? group.cards[0].entity_id
			: group.entity_id
	);
	const subjectId = $derived(effectiveEntityId?.includes(':') ? effectiveEntityId.split(':').pop() : effectiveEntityId);

	const hasBookmark = $derived(group.cards.some((c) => c.bookmarked_at));
	const isGroupSelected = $derived(group.cards.some((c) => c.card_id === selectedCardId));
	const isGroupLastViewed = $derived(!expanded && !isGroupSelected && !hasSelection && !!lastViewedCardId && group.cards.some(c => c.card_id === lastViewedCardId));
	const isDimmed = $derived(hasSelection && !isGroupSelected);

	// Auto-expand when a card in this group is targeted for scroll
	$effect(() => {
		if (scrollToCardId && !expanded && group.cards.some((c) => c.card_id === scrollToCardId)) {
			expanded = true;
		}
	});


	// Close group menu on outside click — uses element ref so clicking
	// another group's menu correctly closes this one.
	$effect(() => {
		if (!groupMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!menuEl?.contains(target)) groupMenuOpen = false;
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};

	const isSmartGroup = $derived(!!group.context_id);
	const isMultiPlatform = $derived(isSmartGroup && (group.platforms?.length ?? 0) > 1);
	const sourceLabel = $derived(
		isSmartGroup && isMultiPlatform
			? 'Multiple'
			: (platformLabel[group.platform] ?? group.platform)
	);
	const sourcesDetail = $derived(
		isSmartGroup && group.platforms
			? group.platforms.map(p => platformLabel[p] ?? p).join(', ')
			: ''
	);

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
			: $cardColors
				? (groupRowStyle[dominantStatus] ?? 'hover:bg-surface-800/60')
				: 'hover:bg-surface-800/60'
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

	let subjectTruncated = $state(false);
	let subjectEl: HTMLSpanElement | undefined = $state();
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
	const hasAnyAction = $derived(canApproveAll || canCompleteAll || canDismissAll || canArchiveAll || canReopenAll || canUnarchiveAll || !!onlink);

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

<div class="transition-opacity {isDimmed ? 'opacity-45 hover:opacity-70' : ''}">
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

		<div class="flex-1 min-w-0 border {expanded ? 'rounded-t-lg border-surface-600 border-b-0 bg-surface-900' : 'rounded-lg border-surface-700/40 ' + groupBgStyle} {isGroupLastViewed ? ($cardColors ? 'card-last-viewed card-last-viewed--compact' : 'card-last-viewed-highlight') : ''} transition-colors" style="{isGroupLastViewed ? '--corner-radius: 0.5rem' : ''}">
			{#if isGroupLastViewed}<div class="card-corner-bottom"></div>{/if}
			<!-- Group header row -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="group/grow flex min-w-0 items-center px-3 py-1.5 cursor-pointer transition-colors"
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
		<span class="w-[60px] shrink-0 flex flex-col" title={isSmartGroup && sourcesDetail ? sourcesDetail : undefined}>
			<span class="text-[11px] font-semibold uppercase tracking-wider text-surface-500 truncate">
				{sourceLabel}
			</span>
			{#if isSmartGroup && sourcesDetail}
				<span class="truncate text-[8px] text-surface-600">{sourcesDetail}</span>
			{/if}
		</span>

		<!-- Subject ID (e.g., FERR-1056) extracted from entity_id -->
		<span class="w-[100px] shrink-0 ml-2 text-[11px] font-medium text-laya-orange/70 truncate">
			{subjectId ?? ''}
		</span>

		<!-- Subject (entity title) -->
		<span class="group/subject relative min-w-0 flex-1 ml-2"
			onmouseenter={() => { if (subjectEl) subjectTruncated = subjectEl.scrollWidth > subjectEl.clientWidth; }}
		>
			<span bind:this={subjectEl} class="block truncate text-xs font-medium text-surface-200">
				{group.entity_title}
			</span>
			{#if subjectTruncated}
				<span class="pointer-events-none absolute top-full left-0 z-10 mt-1 max-w-xs whitespace-normal rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/subject:opacity-100">
					{group.entity_title}
				</span>
			{/if}
		</span>

		<!-- Card count — aligned with ListRow status column (w-[70px]) -->
		<div class="group/count relative w-[70px] shrink-0 flex items-center gap-1 ml-2">
			{#if hasBookmark}
				<svg class="h-3 w-3 shrink-0 text-laya-orange/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" stroke-dasharray="3 2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
				</svg>
			{/if}
			<span class="rounded-full border border-surface-600 bg-surface-700 px-2 py-0.5 text-[10px] font-semibold text-surface-300 cursor-default">
				{group.card_count}
			</span>
			<span class="pointer-events-none absolute top-full left-1/2 -translate-x-1/2 z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/count:opacity-100">
				{statusSummaryTooltip}
			</span>
		</div>

		<!-- Three-dot menu — aligned with ListRow action buttons column (w-[68px]) -->
		<div class="w-[68px] shrink-0 flex items-center justify-end">
			{#if hasAnyAction}
				<div class="group-menu relative" bind:this={menuEl}>
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
							<div class="my-1 border-t border-surface-700"></div>
							<button
								class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange"
								role="menuitem"
								onclick={(e) => { e.stopPropagation(); groupMenuOpen = false; onlink?.(group); }}
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
								Link to...
							</button>
							{#if isSmartGroup}
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-red-400"
									role="menuitem"
									disabled={unlinking}
									onclick={unlinkGroup}
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /><line x1="4" y1="4" x2="20" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
									{unlinking ? 'Unlinking...' : 'Unlink Group'}
								</button>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Persona spacer — matches ListRow w-[62px] -->
		<span class="w-[62px] shrink-0 ml-1"></span>

		<!-- Priority spacer — matches ListRow w-[36px] -->
		<span class="w-[36px] shrink-0 ml-1"></span>

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

	<!-- Expanded: child card rows — checkboxes in gutter, cards in container -->
	{#if expanded}
		<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }}>
			<div class="flex {onbulktoggle ? 'gap-1.5' : ''}">
				<!-- Gutter spacer to align container with header -->
				{#if onbulktoggle}
					<div class="w-5 shrink-0"></div>
				{/if}
				<div class="flex-1 min-w-0 rounded-b-lg border border-t-0 border-surface-600 bg-surface-900 pt-1 pb-0">
					{#each group.cards as card (card.card_id)}
						<div class="flex items-center">
							<!-- Checkbox pulled into the gutter via negative margin -->
							{#if onbulktoggle}
								<div class="w-5 shrink-0 flex items-center justify-center -ml-[26px] mr-[6px]">
									<button
										class="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors
											{bulkSelectedIds?.has(card.card_id)
												? 'bg-laya-orange border-laya-orange'
												: 'border-surface-500 hover:border-surface-300 bg-transparent'}"
										onclick={(e) => { e.stopPropagation(); onbulktoggle(card.card_id, e); }}
										aria-label="{bulkSelectedIds?.has(card.card_id) ? 'Deselect' : 'Select'} card"
									>
										{#if bulkSelectedIds?.has(card.card_id)}
											<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
											</svg>
										{/if}
									</button>
								</div>
							{/if}
							<div class="flex-1 min-w-0">
								<ListRow {card} {onselect} {ondelete} {selectedCardId} indented={true} {hasSelection} {lastViewedCardId} />
							</div>
						</div>
					{/each}
				</div>
			</div>
		</div>
	{/if}
</div>
