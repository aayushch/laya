<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { slide } from 'svelte/transition';
	import ActionCardComponent from './ActionCard.svelte';
	import StatusDot from './StatusDot.svelte';

	import { cardColors } from '$lib/stores/cardColors';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { cardDescriptions } from '$lib/stores/cardDescriptions';
	import { cardSize } from '$lib/stores/cardSize';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { portal } from '$lib/actions/portal';
	import { platformDotColor } from '$lib/utils/cardVisuals';

	let {
		group,
		onselect,
		onselectgroup,
		ondelete,
		onlink,
		selectedCardId = '',
		selectedEntityId = '',
		hasSelection = false,
		lastViewedCardId = '',
		lastViewedEntityId = '',
		scrollToCardId = null,
		detailPanelOpen = false
	}: { group: CardGroup; onselect: (card: ActionCard) => void; onselectgroup?: (group: CardGroup) => void; ondelete?: (cardId: string) => void; onlink?: (group: CardGroup) => void; selectedCardId?: string; selectedEntityId?: string; hasSelection?: boolean; lastViewedCardId?: string; lastViewedEntityId?: string; scrollToCardId?: string | null; detailPanelOpen?: boolean } = $props();

	let expanded = $state(false);
	let wrapperEl = $state<HTMLElement | null>(null);
	let bulkActionRunning = $state(false);
	let groupMenuOpen = $state(false);
	let menuEl: HTMLElement | undefined = $state();
	$effect(() => {
		if (!wrapperEl) return;
		const handler = () => { expanded = true; };
		wrapperEl.addEventListener('expand', handler);
		return () => wrapperEl?.removeEventListener('expand', handler);
	});

	// Truncation detection for conditional tooltips (fixed positioning to escape overflow-hidden)
	let summaryEl: HTMLElement | undefined = $state();
	let statusEl: HTMLElement | undefined = $state();
	let hoveredTooltip: { text: string; top: number; left: number; maxWidth?: number } | null = $state(null);

	function showTooltipIfTruncated(el: HTMLElement | undefined, text: string, opts?: { checkHeight?: boolean; alignRight?: boolean; maxWidth?: number }) {
		if (!el) return;
		const isTruncated = opts?.checkHeight
			? el.scrollHeight > el.clientHeight
			: el.scrollWidth > el.clientWidth;
		if (!isTruncated) { hoveredTooltip = null; return; }
		const rect = el.getBoundingClientRect();
		hoveredTooltip = {
			text,
			top: rect.bottom + 4,
			left: opts?.alignRight ? rect.right : rect.left,
			maxWidth: opts?.maxWidth
		};
	}

	function hideTooltip() { hoveredTooltip = null; }

	// Auto-expand when a card in this group is targeted for scroll
	$effect(() => {
		if (scrollToCardId && !expanded && group.cards.some(c => c.card_id === scrollToCardId)) {
			expanded = true;
		}
	});

	// Auto-collapse when the detail panel slides out (detailPanelOpen goes from true → false).
	// Skip the slide transition to avoid jarring back-to-back animations with scroll-into-view.
	let prevDetailPanelOpen = $state(false);
	let skipCollapseTransition = $state(false);
	$effect(() => {
		if (prevDetailPanelOpen && !detailPanelOpen && expanded) {
			skipCollapseTransition = true;
			expanded = false;
		}
		prevDetailPanelOpen = detailPanelOpen;
	});

	// Close group menu on outside click — uses element ref so clicking
	// another group's menu correctly closes this one.
	$effect(() => {
		if (!groupMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!menuEl?.contains(target)) {
				groupMenuOpen = false;
			}
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const topCard = $derived(group.cards.find((c) => c.status === 'pending') ?? group.cards[0]);
	const extraCount = $derived(group.card_count - 1);

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

	const statusText = $derived(
		statusSummary.map(s => `${s.count} ${statusSummaryLabel[s.status] ?? s.status}`).join(' \u00b7 ') + ` \u00b7 ${group.card_count} cards`
	);

	// Status priority for group color: highest-priority status wins
	const statusPriority: string[] = [
		'pending', 'awaiting_input', 'agent_running', 'failed', 'requires_approval',
		'ready', 'done', 'dismissed', 'archived'
	];

	const glassGroupStatusStyle: Record<string, string> = {
		pending:            'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30  card-pulse-amber',
		ready:              'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30',
		requires_approval:  'glass-card bg-sky-950/45 border-transparent hover:border-sky-700/30',
		done:               'glass-card bg-emerald-950/40 border-transparent hover:border-emerald-700/25',
		failed:             'glass-card bg-rose-950/50   border-transparent   hover:border-rose-700/35',
		dismissed:          'glass-card bg-surface-800/30 border-transparent hover:border-surface-600/30',
		archived:           'glass-card bg-surface-800/30 border-transparent hover:border-surface-600/30',
		agent_running:      'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30 card-pulse-violet',
		awaiting_input:     'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30  card-pulse-amber',
	};

	const solidGroupStatusStyle: Record<string, string> = {
		pending:            'bg-amber-950/55  border-transparent  hover:border-amber-700/45  card-pulse-amber',
		ready:              'bg-amber-950/55  border-transparent  hover:border-amber-700/45',
		requires_approval:  'bg-sky-950/55 border-transparent hover:border-sky-700/40',
		done:               'bg-emerald-950/50 border-transparent hover:border-emerald-700/35',
		failed:             'bg-rose-950/60   border-transparent   hover:border-rose-700/50',
		dismissed:          'bg-surface-800/40 border-transparent hover:border-surface-600/40',
		archived:           'bg-surface-800/40 border-transparent hover:border-surface-600/40',
		agent_running:      'bg-violet-950/55 border-transparent hover:border-violet-700/40 card-pulse-violet',
		awaiting_input:     'bg-amber-950/55  border-transparent  hover:border-amber-700/45  card-pulse-amber',
	};

	const groupStatusStyle = $derived($glassTheme ? glassGroupStatusStyle : solidGroupStatusStyle);

	// Determine dominant status across all cards in the group
	const dominantStatus = $derived.by(() => {
		const statuses = new Set(group.cards.map(c => c.status));
		for (const s of statusPriority) {
			if (statuses.has(s as ActionCard['status'])) return s;
		}
		return group.cards[0]?.status ?? 'pending';
	});

	const neutralGroupStyle = $derived($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/20' : 'bg-surface-800 border-transparent hover:border-surface-600');

	const allArchived = $derived(group.cards.every(c => c.status === 'archived'));
	const groupStyle = $derived(
		allArchived
			? ($glassTheme ? 'glass-card bg-surface-900/30 border-transparent opacity-50 hover:opacity-80' : 'bg-surface-900/60 border-transparent opacity-50 hover:opacity-80')
			: $cardColors
				? (groupStatusStyle[dominantStatus] ?? ($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/20' : 'bg-surface-900 border-transparent hover:border-laya-orange/30'))
				: neutralGroupStyle
	);

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH:     'bg-orange-500 text-orange-50',
		MEDIUM:   'bg-laya-coral/20 text-laya-coral',
		LOW:      'bg-laya-gold/25 text-laya-amber'
	};

	const priorityLabel: Record<string, string> = {
		CRITICAL: 'CRIT',
		HIGH: 'HIGH',
		MEDIUM: 'MED',
		LOW: 'LOW'
	};

	const personaColors: Record<string, string> = {
		ENGINEER: 'text-violet-400',
		COMMS:    'text-emerald-400',
		OPS:      'text-amber-400',
		SALES:    'text-sky-400',
		HR:       'text-rose-400',
		FINANCE:  'text-teal-400'
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
		const days = Math.floor(hours / 24);
		if (days < 7) return `${days}d ago`;
		return `${Math.floor(days / 7)}w ago`;
	}

	// Extract subject ID from entity_id (e.g., "jira:ticket:FERR-1056" → "FERR-1056")
	const subjectId = $derived(group.entity_id?.includes(':') ? group.entity_id.split(':').pop() : group.entity_id);
	const sourceLabel = $derived(platformLabel[group.platform] ?? group.platform);

	// Compact mode: drop the identity row above the title and inline platform/identifier into the footer.
	const compact = $derived($cardSize === 'compact');

	const groupHasWorkspace = $derived(group.cards.some((c) => c.has_workspace));
	const hasBookmark = $derived(group.cards.some((c) => c.bookmarked_at));
	const isGroupSelected = $derived(
		group.cards.some((c) => c.card_id === selectedCardId) ||
		(!!selectedEntityId && group.entity_id === selectedEntityId)
	);
	const isGroupLastViewed = $derived(
		!expanded && !isGroupSelected && !hasSelection && (
			(!!lastViewedCardId && group.cards.some(c => c.card_id === lastViewedCardId)) ||
			(!!lastViewedEntityId && group.entity_id === lastViewedEntityId)
		)
	);
	const isDimmed = $derived(hasSelection && !isGroupSelected);

	// Bulk action menu visibility — only show actions that apply to at least one card
	const canApproveAll = $derived(group.cards.some(c => c.status === 'requires_approval'));
	const canCompleteAll = $derived(group.cards.some(c => c.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(c.status)));
	const canDismissAll = $derived(group.cards.some(c => c.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(c.status)));
	const canArchiveAll = $derived(group.cards.some(c => c.status !== 'archived'));
	const canReopenAll = $derived(group.cards.some(c => ['dismissed', 'archived', 'done'].includes(c.status)));
	const canUnarchiveAll = $derived(group.cards.some(c => c.status === 'archived'));

	const hasAnyAction = $derived(canApproveAll || canCompleteAll || canDismissAll || canArchiveAll || canReopenAll || canUnarchiveAll);

	function toggle() {
		expanded = !expanded;
	}

	let menuPos = $state({ top: 0, right: 0 });

	function toggleGroupMenu(e: Event) {
		e.stopPropagation();
		if (!groupMenuOpen && menuEl) {
			const rect = menuEl.getBoundingClientRect();
			menuPos = { top: rect.bottom + 4, right: window.innerWidth - rect.right };
		}
		groupMenuOpen = !groupMenuOpen;
	}

	function closeGroupMenu() {
		groupMenuOpen = false;
	}

	async function bulkAction(action: 'approve' | 'complete' | 'dismiss' | 'archive' | 'reopen' | 'unarchive', e: Event) {
		e.stopPropagation();
		groupMenuOpen = false;
		bulkActionRunning = true;
		try {
			const promises: Promise<unknown>[] = [];
			for (const card of group.cards) {
				switch (action) {
					case 'approve':
						if (card.status === 'requires_approval') {
							promises.push(engineApi.approveAgent(card.card_id).then(() => { card.status = 'agent_running'; }));
						}
						break;
					case 'complete':
						if (card.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(card.status)) {
							promises.push(engineApi.markCardDone(card.card_id).then(() => { card.status = 'done'; }));
						}
						break;
					case 'dismiss':
						if (card.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(card.status)) {
							promises.push(engineApi.dismissCard(card.card_id).then(() => { card.status = 'dismissed'; }));
						}
						break;
					case 'archive':
						if (card.status !== 'archived') {
							promises.push(engineApi.archiveCard(card.card_id).then(() => { card.status = 'archived'; }));
						}
						break;
					case 'reopen':
						if (['dismissed', 'archived', 'done'].includes(card.status)) {
							promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						}
						break;
					case 'unarchive':
						if (card.status === 'archived') {
							promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						}
						break;
				}
			}
			await Promise.all(promises);
		} finally {
			bulkActionRunning = false;
		}
	}
</script>

<!-- Single persistent DOM — morphs between collapsed card and expanded list -->
<div
	bind:this={wrapperEl}
	class="relative rounded-xl transition-opacity duration-200 hover:z-20 {isDimmed ? ($glassTheme ? 'glass-dim' : 'opacity-45 hover:opacity-70') : isGroupSelected && hasSelection && $glassTheme ? 'glass-focus' : ''}"
	data-card-id={topCard.card_id}
	data-group-entity={group.entity_id}
>
	<!-- Main card / container -->
	<!-- overflow-clip instead of overflow-hidden: hidden creates a scroll container,
		 so scrollIntoView on a selected card can internally scroll this div, pushing
		 the header out of view and leaving empty space at the bottom. clip visually
		 clips the same way but does NOT create a scroll container. -->
	<div
		data-status={$glassTheme && $cardColors && !allArchived && !expanded ? dominantStatus : undefined}
		class="relative rounded-xl border {$glassTheme ? '' : 'shadow-lg'} transition-all duration-200 {expanded ? '' : 'group/card'}
			{expanded
				? ($glassTheme ? 'glass-card border-laya-orange/15 bg-surface-900/50' : 'border-surface-600 bg-surface-900')
				: groupStyle}
			{isGroupLastViewed ? ($cardColors ? 'card-last-viewed' : 'card-last-viewed-highlight') : ''}"
		style="z-index: 1;"
	>
		{#if isGroupLastViewed}<div class="card-corner-bottom"></div>{/if}
		<!-- Header — shared between collapsed and expanded -->
		<div
			role="button"
			tabindex="0"
			class="flex w-full cursor-pointer flex-col gap-1.5 px-4 pt-3 text-left transition-colors
				{expanded ? 'pb-2' : 'pb-0'}"
			onclick={() => {
				if (expanded) {
					expanded = false;
				} else if (onselectgroup) {
					onselectgroup(group);
				} else {
					onselect(topCard);
				}
			}}
			onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (expanded) { expanded = false; } else if (onselectgroup) { onselectgroup(group); } else { onselect(topCard); } } }}
		>
			<!-- Top row: time (left) · utility cluster (right). Time uses latest_at so groups
			     sort coherently against regular cards by most-recent activity. -->
			<div class="flex items-center gap-2">
				<span class="text-[11px] text-surface-400/75 shrink-0">
					{timeAgo(group.latest_at)}
				</span>
				<div class="ml-auto flex items-center gap-1.5">
					{#if hasBookmark}
						<svg class="h-3 w-3 text-laya-orange/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" stroke-dasharray="3 2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
						</svg>
					{/if}
					{#if groupHasWorkspace}
						<span class="text-violet-400/60" title="Has Workspace">
							<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
							</svg>
						</span>
					{/if}
					<!-- Card count badge (expanded only — collapsed uses footer indicator) -->
					{#if expanded}
						<span class="whitespace-nowrap rounded-full bg-laya-orange/10 px-2 py-0.5 text-[10px] font-semibold text-laya-orange">
							{group.card_count} cards
						</span>
					{/if}
					{#if hasAnyAction}
						<!-- Three-dot group actions menu -->
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div class="group-menu relative" bind:this={menuEl}
							onmouseenter={(e) => { if (!groupMenuOpen) { const r = e.currentTarget.getBoundingClientRect(); hoveredTooltip = { text: 'Group actions', top: r.bottom + 4, left: r.left + r.width / 2 }; } }}
							onmouseleave={() => { hoveredTooltip = null; }}
						>
							<button
								onclick={toggleGroupMenu}
								disabled={bulkActionRunning}
								class="flex h-6 w-6 items-center justify-center rounded-full text-laya-orange/70 transition-colors hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-50"
							>
								{#if bulkActionRunning}
									<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
										<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
										<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
									</svg>
								{:else}
									<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
										<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
									</svg>
								{/if}
							</button>
						</div>
					{/if}
					<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
						{priorityLabel[group.top_priority] ?? group.top_priority}
					</span>
					<!-- Chevron — expand/collapse toggle (independent of body click) -->
					<button
						class="shrink-0 rounded p-0.5 transition-colors hover:bg-surface-700/50"
						title={expanded ? 'Collapse' : 'Expand cards'}
						onclick={(e) => { e.stopPropagation(); expanded = !expanded; }}
					>
						<svg class="h-3.5 w-3.5 text-surface-500 transition-transform duration-200 {expanded ? 'rotate-0' : '-rotate-90'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
						</svg>
					</button>
				</div>
			</div>

			<!-- Identity row: subject ID always lives here (close to the header). Relaxed mode
			     also shows the brand dot + source label inline; compact mode hoists the
			     platform down into the footer for vertical compression. -->
			{#if !compact || subjectId}
				<div class="flex items-center gap-1.5 min-w-0">
					{#if !compact}
						<span class="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-surface-500 shrink-0">
							<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(group.platform)}"></span>
							{sourceLabel}
						</span>
					{/if}
					{#if subjectId}
						<span class="text-[10px] font-medium text-laya-orange/70 truncate">{subjectId}</span>
					{/if}
				</div>
			{/if}

			<span class="line-clamp-2 text-sm font-semibold leading-snug {expanded ? 'text-surface-100' : 'text-surface-50'}">
				{group.entity_title}
			</span>
		</div>

		<!-- Collapsed-only content: summary + status footer (instant show/hide) -->
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div class="overflow-hidden cursor-pointer {expanded ? 'hidden' : ''}" role="button" tabindex="0" onclick={() => {
			if (onselectgroup) {
				onselectgroup(group);
			} else {
				onselect(topCard);
			}
		}}>
			<div class="px-4 pb-2">
				<!-- Top card preview summary -->
				{#if $cardDescriptions}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="mb-1.5"
						onmouseenter={() => showTooltipIfTruncated(summaryEl, topCard.summary, { checkHeight: true, maxWidth: 280 })}
						onmouseleave={hideTooltip}
					>
						<p bind:this={summaryEl} class="line-clamp-2 text-xs leading-relaxed text-surface-400">
							{topCard.summary}
						</p>
					</div>
				{/if}

				<!-- Status summary footer -->
				<div class="flex items-center gap-2">
					<div class="flex items-center gap-2 shrink-0 min-w-0">
						{#if topCard.space_name}
							<span class="flex items-center gap-1 shrink-0 text-[10px] text-surface-500">
								<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {topCard.space_color ?? '#F97316'}"></span>
								{topCard.space_name}
							</span>
						{/if}
						{#if compact}
							{#if topCard.space_name}
								<span class="text-[10px] text-surface-600 shrink-0">·</span>
							{/if}
							<span class="flex items-center gap-1 shrink-0 text-[10px] font-semibold uppercase tracking-wider text-surface-500">
								<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(group.platform)}"></span>
								{sourceLabel}
							</span>
						{/if}
					</div>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="ml-auto flex items-center gap-1.5 min-w-0"
						onmouseenter={() => showTooltipIfTruncated(statusEl, statusText, { alignRight: true })}
						onmouseleave={hideTooltip}
					>
						<div bind:this={statusEl} class="flex items-center gap-1.5 min-w-0 overflow-hidden">
							{#each statusSummary as { status, count }}
								<span class="flex items-center gap-1 shrink-0">
									<StatusDot {status} />
									<span class="text-[10px] text-surface-400 whitespace-nowrap">{count} {statusSummaryLabel[status] ?? status}</span>
								</span>
							{/each}
						</div>
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div class="group/stack relative flex items-center gap-1 shrink-0 rounded-md px-1 py-0.5 cursor-pointer transition-colors hover:bg-surface-600/40"
							role="button" tabindex="0"
							onclick={(e) => { e.stopPropagation(); expanded = true; }}
							onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); expanded = true; } }}
							onmouseenter={(e) => {
								const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
								hoveredTooltip = { text: `Show ${group.card_count} cards`, top: rect.bottom + 4, left: rect.left + rect.width / 2 };
							}}
							onmouseleave={hideTooltip}
						>
							<svg class="h-3.5 w-3.5 text-laya-orange" viewBox="0 0 20 20" fill="none">
								<!-- Back card -->
								<rect x="5" y="1" width="11" height="14" rx="2" fill="currentColor" opacity="0.45" stroke="currentColor" stroke-width="1.4" />
								<!-- Front card -->
								<rect x="2" y="4" width="11" height="14" rx="2" fill="currentColor" opacity="0.8" stroke="currentColor" stroke-width="1.4" />
							</svg>
							<span class="text-[10px] font-bold text-laya-orange">{group.card_count}</span>
						</div>
					</div>
				</div>
			</div>
		</div>

		<!-- Expanded-only content: card list (slides in/out) -->
		{#if expanded}
			<div class="space-y-2 px-3 pb-3 pt-1" transition:slide={{ duration: (skipCollapseTransition || $reducedMotion) ? 0 : 200 }}
				onoutroend={() => { skipCollapseTransition = false; }}
			>
				{#each group.cards as card (card.card_id)}
					<ActionCardComponent {card} onselect={onselect} {ondelete} {selectedCardId} {hasSelection} {lastViewedCardId} />
				{/each}
			</div>
		{/if}
	</div>
</div>

{#if groupMenuOpen}
	<!-- Rendered outside wrapper to escape parent opacity/overflow -->
	<div
		use:portal
		class="fixed z-[100] w-40 rounded-lg border p-1 {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-900 shadow-xl shadow-black/50'}"
		style="top: {menuPos.top}px; right: {menuPos.right}px;"
		role="menu"
	>
		{#if canApproveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-violet-400" role="menuitem" onclick={(e) => bulkAction('approve', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
				Approve All
			</button>
		{/if}
		{#if canCompleteAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-green-400" role="menuitem" onclick={(e) => bulkAction('complete', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
				Complete All
			</button>
		{/if}
		{#if canDismissAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400" role="menuitem" onclick={(e) => bulkAction('dismiss', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" /></svg>
				Dismiss All
			</button>
		{/if}
		{#if canReopenAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('reopen', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
				Reopen All
			</button>
		{/if}
		{#if canArchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-surface-400" role="menuitem" onclick={(e) => bulkAction('archive', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
				Archive All
			</button>
		{/if}
		{#if canUnarchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('unarchive', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4l3 3m0 0l3-3m-3 3V9" /></svg>
				Unarchive All
			</button>
		{/if}
		<div class="my-1 border-t border-surface-700"></div>
		<button
			class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange"
			role="menuitem"
			onclick={(e) => { e.stopPropagation(); groupMenuOpen = false; onlink?.(group); }}
		>
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
			Link to...
		</button>
	</div>
{/if}

{#if hoveredTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {hoveredTooltip.top}px; left: {hoveredTooltip.left}px;{hoveredTooltip.maxWidth ? ` max-width: ${hoveredTooltip.maxWidth}px; white-space: normal;` : ' white-space: nowrap; transform: translateX(-100%);'}"
	>
		{hoveredTooltip.text}
	</span>
{/if}
