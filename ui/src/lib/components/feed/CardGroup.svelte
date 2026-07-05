<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
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
	import { parseBackendDate } from '$lib/utils/datetime';
	import PlatformIcon from '$lib/components/settings/PlatformIcon.svelte';

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
	const isContextGroup = $derived(!!group.context_id);

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
		awaiting_input: 'bg-violet-400', staged: 'bg-emerald-400'
	};

	const statusSummaryLabel: Record<string, string> = {
		pending: 'Pending', approved: 'Approved', dismissed: 'Dismissed',
		archived: 'Archived', completed: 'Completed', failed: 'Failed',
		executing: 'Executing', agent_running: 'Running',
		awaiting_input: 'Needs Input', staged: 'Staged'
	};

	const statusText = $derived(
		statusSummary.map(s => `${s.count} ${statusSummaryLabel[s.status] ?? s.status}`).join(' \u00b7 ') + ` \u00b7 ${group.card_count} cards` + (group.unread_count > 0 ? ` \u00b7 ${group.unread_count} new` : '')
	);

	// Status priority for group color: highest-priority status wins
	const statusPriority: string[] = [
		'pending', 'awaiting_input', 'agent_running', 'failed',
		'ready', 'done', 'dismissed', 'archived'
	];

	const glassGroupStatusStyle: Record<string, string> = {
		pending:            'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30  card-pulse-amber',
		ready:              'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30',
		done:               'glass-card bg-emerald-950/40 border-transparent hover:border-emerald-700/25',
		failed:             'glass-card bg-rose-950/50   border-transparent   hover:border-rose-700/35',
		dismissed:          'glass-card bg-surface-800/30 border-transparent hover:border-surface-600/30',
		archived:           'glass-card bg-surface-800/30 border-transparent hover:border-surface-600/30',
		agent_running:      'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30 card-pulse-violet',
		awaiting_input:     'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30',
	};
	const glassWorkspaceGroupStyle = 'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30';

	const solidGroupStatusStyle: Record<string, string> = {
		pending:            'bg-amber-950/55  border-transparent  hover:border-amber-700/45  card-pulse-amber',
		ready:              'bg-amber-950/55  border-transparent  hover:border-amber-700/45',
		done:               'bg-emerald-950/50 border-transparent hover:border-emerald-700/35',
		failed:             'bg-rose-950/60   border-transparent   hover:border-rose-700/50',
		dismissed:          'bg-surface-800/40 border-transparent hover:border-surface-600/40',
		archived:           'bg-surface-800/40 border-transparent hover:border-surface-600/40',
		agent_running:      'bg-violet-950/55 border-transparent hover:border-violet-700/40 card-pulse-violet',
		awaiting_input:     'bg-violet-950/55 border-transparent hover:border-violet-700/40',
	};
	const solidWorkspaceGroupStyle = 'bg-violet-950/55 border-transparent hover:border-violet-700/40';

	const groupStatusStyle = $derived($glassTheme ? glassGroupStatusStyle : solidGroupStatusStyle);
	const workspaceGroupStyle = $derived($glassTheme ? glassWorkspaceGroupStyle : solidWorkspaceGroupStyle);

	// Determine dominant status across all cards in the group
	const dominantStatus = $derived.by(() => {
		const statuses = new Set(group.cards.map(c => c.status));
		for (const s of statusPriority) {
			if (statuses.has(s as ActionCard['status'])) return s;
		}
		return group.cards[0]?.status ?? 'pending';
	});

	const neutralGroupStyle = $derived($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/20' : 'bg-surface-800 border-transparent hover:border-surface-600');

	const terminalStatuses = new Set(['done', 'failed', 'dismissed', 'archived']);

	const allArchived = $derived(group.cards.every(c => c.status === 'archived'));
	// A running child must glow the collapsed group card even when a higher-priority
	// sibling status (pending/awaiting_input) wins dominantStatus — common in context
	// (linked) groups whose cards span multiple entities. Without this the running glow
	// never propagates from the child to the group.
	const hasRunningChild = $derived(group.cards.some(c => c.status === 'agent_running'));
	const groupStyle = $derived.by(() => {
		if (allArchived) return $glassTheme ? 'glass-card bg-surface-900/30 border-transparent opacity-50 hover:opacity-80' : 'bg-surface-900/60 border-transparent opacity-50 hover:opacity-80';
		if (!$cardColors) return neutralGroupStyle;
		let style: string;
		if (groupHasWorkspace && !terminalStatuses.has(dominantStatus)) {
			style = dominantStatus === 'agent_running' ? groupStatusStyle['agent_running'] : workspaceGroupStyle;
		} else {
			style = groupStatusStyle[dominantStatus] ?? ($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/20' : 'bg-surface-900 border-transparent hover:border-laya-orange/30');
		}
		// dominantStatus drives the base tint; add the violet pulse on top so the running
		// signal isn't suppressed by a sibling's higher-priority status.
		if (hasRunningChild && !style.includes('card-pulse-violet')) {
			style += ' card-pulse-violet';
		}
		return style;
	});

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
		awaiting_input: 'bg-violet-400',
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
		const d = parseBackendDate(dateStr);
		if (!d) return '';
		const diff = Date.now() - d.getTime();
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
	// The card whose workspace the group-level indicator opens (first card with one).
	const workspaceCardId = $derived(group.cards.find((c) => c.has_workspace)?.card_id);
	const visualDominantStatus = $derived(
		groupHasWorkspace && !terminalStatuses.has(dominantStatus) && dominantStatus !== 'agent_running'
			? 'awaiting_input'
			: dominantStatus
	);
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
	const canCompleteAll = $derived(group.cards.some(c => c.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(c.status)));
	const canDismissAll = $derived(group.cards.some(c => c.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(c.status)));
	const canArchiveAll = $derived(group.cards.some(c => c.status !== 'archived'));
	const canReopenAll = $derived(group.cards.some(c => ['dismissed', 'archived', 'done'].includes(c.status)));
	const canUnarchiveAll = $derived(group.cards.some(c => c.status === 'archived'));

	const hasAnyAction = $derived(canCompleteAll || canDismissAll || canArchiveAll || canReopenAll || canUnarchiveAll);

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

	async function bulkAction(action: 'complete' | 'dismiss' | 'archive' | 'reopen' | 'unarchive', e: Event) {
		e.stopPropagation();
		groupMenuOpen = false;
		bulkActionRunning = true;
		try {
			const promises: Promise<unknown>[] = [];
			for (const card of group.cards) {
				switch (action) {
					case 'complete':
						if (card.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(card.status)) {
							promises.push(engineApi.markCardDone(card.card_id).then(() => { card.status = 'done'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						}
						break;
					case 'dismiss':
						if (card.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(card.status)) {
							promises.push(engineApi.dismissCard(card.card_id).then(() => { card.status = 'dismissed'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						}
						break;
					case 'archive':
						if (card.status !== 'archived') {
							promises.push(engineApi.archiveCard(card.card_id).then(() => { card.status = 'archived'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						}
						break;
					case 'reopen':
						if (['dismissed', 'archived', 'done'].includes(card.status)) {
							// Use the backend's restored status, not a hardcoded 'ready' —
						// reopen restores the card's saved previous_status which may be
						// pending/requires_approval (review §2 UI — P4-31).
						promises.push(engineApi.reopenCard(card.card_id).then((r) => { card.status = r.status as ActionCard['status']; }));
						}
						break;
					case 'unarchive':
						if (card.status === 'archived') {
							// Use the backend's restored status, not a hardcoded 'ready' —
						// reopen restores the card's saved previous_status which may be
						// pending/requires_approval (review §2 UI — P4-31).
						promises.push(engineApi.reopenCard(card.card_id).then((r) => { card.status = r.status as ActionCard['status']; }));
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
		data-status={$glassTheme && $cardColors && !allArchived && !expanded ? visualDominantStatus : undefined}
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
				<span class="text-laya-secondary text-surface-400/75 shrink-0">
					{timeAgo(group.latest_at)}
				</span>
				{#if isContextGroup}
					<span class="linked-badge inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-medium tracking-wide shrink-0">
						<svg class="h-2.5 w-2.5 shrink-0" viewBox="0 0 16 16" fill="none">
							<rect x="1" y="1" width="8" height="8" rx="2" stroke="currentColor" stroke-width="1.3" />
							<rect x="7" y="7" width="8" height="8" rx="2" stroke="currentColor" stroke-width="1.3" />
							<circle cx="8" cy="8" r="1.5" fill="currentColor" />
						</svg>
						Linked
					</span>
				{/if}
				<div class="ml-auto flex items-center gap-1.5">
					<!-- Card count badge (expanded only — collapsed uses footer indicator).
					     Lives at the start of the cluster so the icons/menu/chevron keep
					     their positions when the group expands. -->
					{#if expanded}
						<span class="whitespace-nowrap rounded-full bg-laya-orange/10 px-2 py-0.5 text-laya-micro font-semibold text-laya-orange" title="{group.card_count} cards">
							{group.card_count}
						</span>
					{/if}
					{#if hasBookmark}
						<svg class="h-3 w-3 text-laya-orange/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" stroke-dasharray="3 2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
						</svg>
					{/if}
					{#if groupHasWorkspace && workspaceCardId}
						<!-- Workspace indicator doubles as a shortcut: clicking it opens the workspace directly. -->
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<a
							href="/workspace/{workspaceCardId}"
							aria-label="Open Workspace"
							class="flex h-6 w-6 items-center justify-center rounded-md text-violet-400/60 transition-colors hover:bg-violet-500/15 hover:text-violet-400"
							onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/workspace/${workspaceCardId}`); }}
							onmouseenter={(e) => { const r = e.currentTarget.getBoundingClientRect(); hoveredTooltip = { text: 'Open Workspace', top: r.bottom + 4, left: r.left + r.width / 2 }; }}
							onmouseleave={() => { hoveredTooltip = null; }}
						>
							<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
							</svg>
						</a>
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
					<span class="rounded px-1.5 py-0.5 text-laya-micro font-bold uppercase {priorityColors[group.top_priority] ?? priorityColors.MEDIUM}">
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
			     platform down into the footer for vertical compression.
			     Context groups skip this row — their linked indicator is in the top row. -->
			{#if !isContextGroup && (!compact || subjectId)}
				<div class="flex items-center gap-1.5 min-w-0">
					{#if !compact}
						<span class="flex items-center gap-1.5 text-laya-micro font-semibold uppercase tracking-widest text-surface-500 shrink-0">
							<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(group.platform)}"></span>
							{sourceLabel}
						</span>
					{/if}
					{#if subjectId}
						<span class="text-laya-micro font-medium text-laya-orange/70 truncate">{subjectId}</span>
					{/if}
				</div>
			{/if}

			<span class="line-clamp-2 text-laya-base {group.unread_count > 0 ? 'font-bold text-surface-50' : 'font-normal text-surface-200'} leading-snug">
				{group.context_label ?? group.entity_title}
			</span>
			{#if group.tags?.length}
				<div class="flex flex-wrap gap-1 mt-1">
					{#each group.tags as tag}
						<span
							class="{tag.is_system ? 'tag-chip-system' : 'tag-chip-user'} inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none"
							style="--tag-color: {tag.color ?? (tag.is_system ? '#6B7280' : '#C4956B')}"
						>
							{tag.tag_name}
						</span>
					{/each}
				</div>
			{/if}
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
						<p bind:this={summaryEl} class="line-clamp-2 text-laya-secondary leading-relaxed text-surface-400">
							{topCard.summary}
						</p>
					</div>
				{/if}

				<!-- Status summary footer -->
				<div class="flex items-center gap-2">
					<div class="flex items-center gap-2 shrink-0 min-w-0">
						{#if isContextGroup}
							<span class="text-laya-orange/60 shrink-0">
								<svg class="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none">
									<rect x="1" y="1" width="8" height="8" rx="2" stroke="currentColor" stroke-width="1.3" />
									<rect x="7" y="7" width="8" height="8" rx="2" stroke="currentColor" stroke-width="1.3" />
									<circle cx="8" cy="8" r="1.5" fill="currentColor" />
								</svg>
							</span>
						{:else if compact}
							<span class="text-laya-orange shrink-0">
								<PlatformIcon platform={group.platform} size={14} />
							</span>
						{/if}
						{#if topCard.space_name}
							<span class="flex items-center gap-1 shrink-0 text-laya-micro text-surface-500">
								<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {topCard.space_color ?? '#F97316'}"></span>
								{topCard.space_name}
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
								<span class="flex items-center gap-1 shrink-0 {status === 'awaiting_input' ? 'status-glow-violet' : ''}">
									<StatusDot {status} />
									<span class="text-laya-micro text-surface-400 whitespace-nowrap">{count} {statusSummaryLabel[status] ?? status}</span>
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
							<span class="text-laya-micro font-bold text-laya-orange">{group.card_count}</span>
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
				{#each group.cards as card, i (card.card_id)}
					{@const prevEntityId = i > 0 ? group.cards[i - 1].entity_id : null}
					{@const showEntityDivider = isContextGroup && card.entity_id && (i === 0 || (prevEntityId && card.entity_id !== prevEntityId))}
					{#if showEntityDivider}
						<div class="flex items-center gap-2 py-0.5 px-1">
							<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(card.entity_id?.split(':')[0] ?? '')}"></span>
							<span class="text-[10px] font-medium uppercase tracking-wider text-surface-500 truncate">{card.entity_id?.split(':').pop()}</span>
							<div class="flex-1 border-t border-surface-700/50"></div>
						</div>
					{/if}
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
		{#if canCompleteAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-green-400" role="menuitem" onclick={(e) => bulkAction('complete', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
				Complete All
			</button>
		{/if}
		{#if canDismissAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400" role="menuitem" onclick={(e) => bulkAction('dismiss', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" /></svg>
				Dismiss All
			</button>
		{/if}
		{#if canReopenAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('reopen', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
				Reopen All
			</button>
		{/if}
		{#if canArchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-surface-400" role="menuitem" onclick={(e) => bulkAction('archive', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
				Archive All
			</button>
		{/if}
		{#if canUnarchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('unarchive', e)}>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4l3 3m0 0l3-3m-3 3V9" /></svg>
				Unarchive All
			</button>
		{/if}
		<div class="my-1 border-t border-surface-700"></div>
		<button
			class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-laya-orange"
			role="menuitem"
			onclick={(e) => { e.stopPropagation(); groupMenuOpen = false; onlink?.(group); }}
		>
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
			Link to...
		</button>
		{#if isContextGroup && group.context_id}
			<button
				class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400"
				role="menuitem"
				onclick={async (e) => {
					e.stopPropagation();
					groupMenuOpen = false;
					try {
						await engineApi.unlinkContextGroup(group.context_id!);
					} catch { /* reload will handle */ }
				}}
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" /></svg>
				Unlink Group
			</button>
		{/if}
	</div>
{/if}

{#if hoveredTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] rounded-md border border-transparent glass-tooltip px-2 py-1 text-laya-micro font-medium"
		style="top: {hoveredTooltip.top}px; left: {hoveredTooltip.left}px;{hoveredTooltip.maxWidth ? ` max-width: ${hoveredTooltip.maxWidth}px; white-space: normal;` : ' white-space: nowrap; transform: translateX(-100%);'}"
	>
		{hoveredTooltip.text}
	</span>
{/if}
