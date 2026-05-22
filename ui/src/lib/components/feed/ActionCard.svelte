<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
	import { chatOpen, chatCardContext, chatCardIds, chatListOpen } from '$lib/stores/chat';
	import { buildSingleCardContext } from '$lib/utils/cardContext';
	import { cardColors } from '$lib/stores/cardColors';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { cardDescriptions } from '$lib/stores/cardDescriptions';
	import { cardSize } from '$lib/stores/cardSize';
	import { portal } from '$lib/actions/portal';
	import StatusDot from './StatusDot.svelte';
	import PlatformIcon from '$lib/components/settings/PlatformIcon.svelte';
	import { platformDotColor, platformKey, actorInitials, actorAvatarColor } from '$lib/utils/cardVisuals';

	let { card, onselect, ondelete, onlink, selectedCardId = '', hasSelection = false, lastViewedCardId = '' }: { card: ActionCard; onselect: (card: ActionCard) => void; ondelete?: (cardId: string) => void; onlink?: (card: ActionCard) => void; selectedCardId?: string; hasSelection?: boolean; lastViewedCardId?: string } = $props();

	const isSelected = $derived(card.card_id === selectedCardId);
	// Show a persistent left accent bar on the last-viewed card after the detail panel closes
	const isLastViewed = $derived(!isSelected && !hasSelection && card.card_id === lastViewedCardId);

	let markingDone = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let showDeleteConfirm = $state(false);
	let deleting = $state(false);
	let actionMenuOpen = $state(false);
	let actionMenuEl: HTMLElement | undefined = $state();
	let actionMenuPos = $state({ top: 0, left: 0 });
	let bookmarking = $state(false);

	// Fixed-position tooltip — rendered outside the glass-card stacking context
	let headerEl: HTMLElement | undefined = $state();
	let summaryEl: HTMLElement | undefined = $state();
	let srcRefEl: HTMLElement | undefined = $state();
	let actorEl: HTMLElement | undefined = $state();
	let timeEl: HTMLElement | undefined = $state();

	let fixedTooltip = $state<{ text: string; top: number; left: number; maxWidth?: number } | null>(null);

	function showTooltip(el: HTMLElement, text: string, opts?: { maxWidth?: number }) {
		const rect = el.getBoundingClientRect();
		fixedTooltip = { text, top: rect.bottom + 4, left: rect.left, maxWidth: opts?.maxWidth };
	}

	function showTooltipIfTruncated(el: HTMLElement | undefined, text: string, opts?: { checkHeight?: boolean; maxWidth?: number }) {
		if (!el) return;
		const isTruncated = opts?.checkHeight
			? el.scrollHeight > el.clientHeight
			: el.scrollWidth > el.clientWidth;
		if (!isTruncated) { fixedTooltip = null; return; }
		showTooltip(el, text, opts);
	}

	function hideTooltip() { fixedTooltip = null; }

	// Close action overflow menu on outside click — swallow the event so it
	// doesn't propagate to the card and trigger a selection.  Uses element
	// ref so clicking another card's menu correctly closes this one.
	$effect(() => {
		if (!actionMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!actionMenuEl?.contains(target)) {
				e.stopPropagation();
				e.preventDefault();
				actionMenuOpen = false;
			}
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const isArchived = $derived(card.status === 'archived');

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH:     'bg-rose-500/25 text-rose-300',
		MEDIUM:   'bg-amber-500/20 text-amber-300',
		LOW:      'bg-surface-700/40 text-surface-400'
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
		pending:            'bg-yellow-400 animate-pulse',
		ready:              'bg-amber-400',

		agent_running:      'bg-violet-400 animate-pulse',
		awaiting_input:     'bg-violet-400',
		done:               'bg-green-500',
		failed:             'bg-red-500',
		dismissed:          'bg-surface-500',
		archived:           'bg-surface-600'
	};

	const statusLabel: Record<string, string> = {
		pending:            'Processing',
		ready:              'Ready',

		agent_running:      'Agent Running',
		awaiting_input:     'Input Needed',
		done:               'Done',
		failed:             'Failed',
		dismissed:          'Dismissed',
		archived:           'Archived'
	};

	const platformLabel: Record<string, string> = {
		jira:      'Jira',
		gmail:     'Gmail',
		slack:     'Slack',
		bitbucket: 'Bitbucket',
		calendar:  'Calendar',
		github:    'GitHub',
		laya:      'Laya'
	};

	const glassStatusCardStyle: Record<string, string> = {
		pending:            'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30  card-pulse-amber',
		ready:              'glass-card bg-amber-950/45  border-transparent  hover:border-amber-700/30',

		agent_running:      'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30 card-pulse-violet',
		awaiting_input:     'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30',
		done:               'glass-card bg-emerald-950/40 border-transparent hover:border-emerald-700/25',
		failed:             'glass-card bg-rose-950/50   border-transparent   hover:border-rose-700/35',
		dismissed:          'glass-card bg-surface-800/30 border-transparent hover:border-surface-600/30 opacity-50 hover:opacity-75',
	};
	const glassWorkspaceCardStyle = 'glass-card bg-violet-950/45 border-transparent hover:border-violet-700/30';

	const solidStatusCardStyle: Record<string, string> = {
		pending:            'bg-amber-950/55  border-transparent  hover:border-amber-700/45  card-pulse-amber',
		ready:              'bg-amber-950/55  border-transparent  hover:border-amber-700/45',

		agent_running:      'bg-violet-950/55 border-transparent hover:border-violet-700/40 card-pulse-violet',
		awaiting_input:     'bg-violet-950/55 border-transparent hover:border-violet-700/40',
		done:               'bg-emerald-950/50 border-transparent hover:border-emerald-700/35',
		failed:             'bg-rose-950/60   border-transparent   hover:border-rose-700/50',
		dismissed:          'bg-surface-800/40 border-transparent hover:border-surface-600/40 opacity-50 hover:opacity-75',
	};
	const solidWorkspaceCardStyle = 'bg-violet-950/55 border-transparent hover:border-violet-700/40';

	const statusCardStyle = $derived($glassTheme ? glassStatusCardStyle : solidStatusCardStyle);
	const workspaceCardStyle = $derived($glassTheme ? glassWorkspaceCardStyle : solidWorkspaceCardStyle);
	const neutralCardStyle = $derived($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/35' : 'bg-surface-800 border-transparent hover:border-surface-600');

	const terminalStatuses = new Set(['done', 'failed', 'dismissed', 'archived']);

	const baseCardStyle = $derived.by(() => {
		if (isArchived) return $glassTheme ? 'glass-card bg-surface-900/30 border-transparent opacity-50 hover:opacity-80' : 'bg-surface-900/60 border-transparent opacity-50 hover:opacity-80';
		if (!$cardColors) return neutralCardStyle;
		if (card.has_workspace && !terminalStatuses.has(card.status)) {
			if (card.status === 'agent_running') return statusCardStyle['agent_running'];
			return workspaceCardStyle;
		}
		return statusCardStyle[card.status] ?? ($glassTheme ? 'glass-card bg-surface-800/40 border-transparent hover:border-laya-orange/35' : 'bg-surface-800 border-transparent hover:border-laya-orange/30');
	});

	const visualStatus = $derived(
		card.has_workspace && !terminalStatuses.has(card.status) && card.status !== 'agent_running'
			? 'awaiting_input'
			: card.status
	);

	const isDimmed = $derived(!isSelected && hasSelection && !isArchived);

	const dimClass = $derived(isDimmed ? ($glassTheme ? ' glass-dim' : ' opacity-45 hover:opacity-70') : '');
	const focusClass = $derived(isSelected && hasSelection && $glassTheme ? ' glass-focus' : '');

	const cardStyle = $derived(
		`${baseCardStyle}${dimClass}${focusClass}${isLastViewed ? ($cardColors ? ' card-last-viewed' : ' card-last-viewed-highlight') : ''}`
	);

	const platform = $derived(
		card.entity_id
			? (platformLabel[card.entity_id.split(':')[0]] ?? card.entity_id.split(':')[0])
			: ''
	);

	// Compact mode collapses metadata rows into the footer and tightens internal
	// spacing. The platform/source row, the standalone space row, and the footer
	// hairline all disappear; their information moves inline next to the actor.
	const compact = $derived($cardSize === 'compact');

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
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

	function fullDate(dateStr?: string): string {
		if (!dateStr) return '';
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const d = new Date(utcStr);
		return d.toLocaleString(undefined, {
			month: 'short', day: 'numeric', year: 'numeric',
			hour: 'numeric', minute: '2-digit'
		});
	}

	async function markDone(e: Event) {
		e.stopPropagation();
		markingDone = true;
		try {
			await engineApi.markCardDone(card.card_id);
			card.status = 'done';
			if (!card.read_at) card.read_at = new Date().toISOString();
		} finally {
			markingDone = false;
		}
	}

	async function dismiss(e: Event) {
		e.stopPropagation();
		dismissing = true;
		try {
			await engineApi.dismissCard(card.card_id);
			card.status = 'dismissed';
			if (!card.read_at) card.read_at = new Date().toISOString();
		} finally {
			dismissing = false;
		}
	}

	async function archive(e: Event) {
		e.stopPropagation();
		archiving = true;
		try {
			await engineApi.archiveCard(card.card_id);
			card.status = 'archived';
			if (!card.read_at) card.read_at = new Date().toISOString();
		} finally {
			archiving = false;
		}
	}

	async function reopen(e: Event) {
		e.stopPropagation();
		reopening = true;
		try {
			const result = await engineApi.reopenCard(card.card_id);
			card.status = result.status as ActionCard['status'];
		} finally {
			reopening = false;
		}
	}

	function deleteCard(e: Event) {
		e.stopPropagation();
		showDeleteConfirm = false;
		ondelete?.(card.card_id);
		engineApi.deleteCard(card.card_id).catch(() => {});
	}

	async function toggleBookmark(e: Event) {
		e.stopPropagation();
		bookmarking = true;
		try {
			if (card.bookmarked_at) {
				await engineApi.unbookmarkCard(card.card_id);
				card.bookmarked_at = undefined;
			} else {
				const result = await engineApi.bookmarkCard(card.card_id);
				card.bookmarked_at = result.bookmarked_at;
			}
		} finally {
			bookmarking = false;
		}
	}

	function chatAbout(e: Event) {
		e.stopPropagation();
		chatCardContext.set(buildSingleCardContext(card));
		chatCardIds.set([card.card_id]);
		chatListOpen.set(false);
		chatOpen.set(true);
	}
</script>

<div
	role="button"
	tabindex="0"
	data-card-id={card.card_id}
	data-status={$glassTheme && $cardColors && !isArchived ? visualStatus : undefined}
	class="group/card relative flex min-h-0 w-full cursor-pointer flex-col rounded-xl border {$glassTheme ? '' : 'shadow-lg'} px-4 pb-2 pt-3 text-left transition-colors hover:z-20 {cardStyle}"
	onclick={() => onselect(card)}
	onkeydown={(e) => e.key === 'Enter' && onselect(card)}
>
	{#if isLastViewed}<div class="card-corner-bottom"></div>{/if}
	<!-- Row 1: Time (rest) ⇄ Action icons (hover) on left, utility icons + status + priority on right.
	     Layered swap: both children absolutely positioned in a 100px-wide / 24px-tall slot so the
	     right-side cluster never shifts when the swap happens. -->
	<div class="{compact ? 'mb-1' : 'mb-2'} flex items-center justify-between">
		<div class="relative w-[100px] h-6 shrink-0">
			<!-- Time at rest — plain text, no icon -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<span
				bind:this={timeEl}
				class="absolute inset-0 flex items-center text-laya-secondary text-surface-400/75 opacity-100 transition-opacity duration-[180ms] ease-out group-hover/card:opacity-0 group-hover/card:pointer-events-none"
				onmouseenter={() => timeEl && showTooltip(timeEl, fullDate(card.created_at))}
				onmouseleave={hideTooltip}
			>{timeAgo(card.created_at)}</span>
			<!-- Action icons revealed on hover -->
			<div class="absolute inset-0 flex items-center gap-1 opacity-0 pointer-events-none transition-opacity duration-[180ms] ease-out group-hover/card:opacity-100 group-hover/card:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto">
			{#if card.status === 'ready'}
				<!-- Mark as Done -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Mark as Done')} onmouseleave={hideTooltip}>
					<button
						aria-label="Mark as Done"
						class="flex h-6 w-6 items-center justify-center rounded-md text-green-400/60 transition-all hover:bg-green-500/15 hover:text-green-400 disabled:opacity-40"
						onclick={markDone}
						disabled={markingDone}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
						</svg>
					</button>
				</div>
				<!-- Dismiss -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Dismiss')} onmouseleave={hideTooltip}>
					<button
						aria-label="Dismiss"
						class="flex h-6 w-6 items-center justify-center rounded-md text-surface-500 transition-all hover:bg-surface-500/15 hover:text-surface-300 disabled:opacity-40"
						onclick={dismiss}
						disabled={dismissing}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				</div>
				{#if card.has_workspace}
					<!-- Overflow: Archive + Workspace -->
					<div class="action-overflow-menu relative" bind:this={actionMenuEl}>
						<button
							class="flex h-6 w-6 items-center justify-center rounded-md text-surface-500 transition-all hover:bg-surface-700/50 hover:text-surface-300"
							onclick={(e) => { e.stopPropagation(); if (!actionMenuOpen && actionMenuEl) { const r = actionMenuEl.getBoundingClientRect(); actionMenuPos = { top: r.bottom + 4, left: r.left }; } actionMenuOpen = !actionMenuOpen; }}
							aria-label="More actions"
						>
							<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
								<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
							</svg>
						</button>
						{#if actionMenuOpen}
							<div use:portal class="fixed z-[100] w-40 rounded-lg border p-1 {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}" style="top: {actionMenuPos.top}px; left: {actionMenuPos.left}px;" role="menu">
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400 disabled:opacity-40"
									role="menuitem"
									onclick={(e) => { actionMenuOpen = false; archive(e); }}
									disabled={archiving}
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
									Archive
								</button>
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-laya-secondary text-surface-300 transition-colors hover:bg-surface-700 hover:text-violet-400"
									role="menuitem"
									onclick={(e) => { e.stopPropagation(); actionMenuOpen = false; goto(`/workspace/${card.card_id}`); }}
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
									Workspace
								</button>
							</div>
						{/if}
					</div>
				{:else}
					<!-- No workspace — Archive fits as 3rd button -->
					<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
						<button
							aria-label="Archive"
							class="flex h-6 w-6 items-center justify-center rounded-md text-red-400/60 transition-all hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
							onclick={archive}
							disabled={archiving}
						>
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
							</svg>
						</button>
					</div>
				{/if}
			{:else if card.status === 'dismissed'}
				<!-- Reopen -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Reopen')} onmouseleave={hideTooltip}>
					<button
						aria-label="Reopen"
						class="flex h-6 w-6 items-center justify-center rounded-md text-laya-orange/60 transition-all hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40"
						onclick={reopen}
						disabled={reopening}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4" />
						</svg>
					</button>
				</div>
				<!-- Archive -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
					<button
						aria-label="Archive"
						class="flex h-6 w-6 items-center justify-center rounded-md text-red-400/60 transition-all hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
						onclick={archive}
						disabled={archiving}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>
				</div>
			{:else if card.status === 'archived'}
				<!-- Unarchive -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Unarchive')} onmouseleave={hideTooltip}>
					<button
						aria-label="Unarchive"
						class="flex h-6 w-6 items-center justify-center rounded-md text-laya-orange/60 transition-all hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40"
						onclick={reopen}
						disabled={reopening}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4" />
						</svg>
					</button>
				</div>
				<!-- Delete -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Delete')} onmouseleave={hideTooltip}>
					<button
						aria-label="Delete"
						class="flex h-6 w-6 items-center justify-center rounded-md text-red-400/60 transition-all hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
						onclick={(e) => { e.stopPropagation(); showDeleteConfirm = true; }}
						disabled={deleting}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
						</svg>
					</button>
				</div>
			{:else if card.status === 'done'}
				<!-- Reopen -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Reopen')} onmouseleave={hideTooltip}>
					<button
						aria-label="Reopen"
						class="flex h-6 w-6 items-center justify-center rounded-md text-laya-orange/60 transition-all hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40"
						onclick={reopen}
						disabled={reopening}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4" />
						</svg>
					</button>
				</div>
				<!-- Archive -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
					<button
						aria-label="Archive"
						class="flex h-6 w-6 items-center justify-center rounded-md text-red-400/60 transition-all hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
						onclick={archive}
						disabled={archiving}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>
				</div>
			{:else if card.status === 'failed'}
				<!-- Retry -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Retry')} onmouseleave={hideTooltip}>
					<button
						aria-label="Retry"
						class="flex h-6 w-6 items-center justify-center rounded-md text-laya-orange/60 transition-all hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40"
						onclick={reopen}
						disabled={reopening}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M1 4v6h6" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
						</svg>
					</button>
				</div>
				<!-- Archive -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
					<button
						aria-label="Archive"
						class="flex h-6 w-6 items-center justify-center rounded-md text-surface-500 transition-all hover:bg-surface-500/15 hover:text-surface-300 disabled:opacity-40"
						onclick={archive}
						disabled={archiving}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>
				</div>
			{:else if card.status === 'awaiting_input'}
				<!-- Archive -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
					<button
						aria-label="Archive"
						class="flex h-6 w-6 items-center justify-center rounded-md text-red-400/60 transition-all hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
						onclick={archive}
						disabled={archiving}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
					</button>
				</div>
			{/if}
			{#if card.has_workspace && card.status !== 'ready'}
				<!-- Open Workspace — shown as standalone button only for statuses with ≤2 actions -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Open Workspace')} onmouseleave={hideTooltip}>
					<a
						href="/workspace/{card.card_id}"
						aria-label="Open Workspace"
						class="flex h-6 w-6 items-center justify-center rounded-md text-violet-400/60 transition-all hover:bg-violet-500/15 hover:text-violet-400"
						onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/workspace/${card.card_id}`); }}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
						</svg>
					</a>
				</div>
			{/if}
		</div>
		</div>

		<!-- Right: utility icons (appear on hover) + priority chip -->
		<div class="flex items-center gap-0.5 min-w-0">
			<!-- Link to (only for standalone cards, not cards inside groups) -->
			{#if onlink}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, 'Link to...')} onmouseleave={hideTooltip}>
					<span
						role="button"
						tabindex="0"
						onclick={(e: Event) => { e.stopPropagation(); onlink(card); }}
						onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') { e.stopPropagation(); onlink(card); }}}
						class="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md transition-all
							text-surface-400 hover:bg-laya-orange/15 hover:text-laya-orange opacity-0 group-hover/card:opacity-100"
					>
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
						</svg>
					</span>
				</div>
			{/if}
			<!-- Bookmark -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="relative" onmouseenter={(e) => showTooltip(e.currentTarget, card.bookmarked_at ? 'Remove Bookmark' : 'Bookmark')} onmouseleave={hideTooltip}>
				<span
					role="button"
					tabindex="0"
					onclick={toggleBookmark}
					onkeydown={(e) => e.key === 'Enter' && toggleBookmark(e)}
					class="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md transition-all disabled:opacity-40
						{card.bookmarked_at
							? 'text-laya-orange'
							: 'text-surface-400 hover:bg-laya-orange/15 hover:text-laya-orange opacity-0 group-hover/card:opacity-100'}"
				>
					<svg class="h-3 w-3" fill={card.bookmarked_at ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
					</svg>
				</span>
			</div>
			{#if card.has_workspace}
				<span class="ml-1 shrink-0 text-violet-400/60" title="Has Workspace">
					<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
					</svg>
				</span>
			{/if}
			<!-- Status indicator -->
			<span class="ml-1 flex items-center gap-1 min-w-0 overflow-hidden {card.status === 'awaiting_input' ? 'status-glow-violet' : ''}">
				<StatusDot status={card.status} size="md" errorMessage={card.last_error} />
				<span class="text-laya-secondary text-surface-400 truncate" title={card.status === 'failed' && card.last_error ? card.last_error : ''}>{statusLabel[card.status] ?? card.status}</span>
			</span>
			<!-- Priority chip -->
			<span class="ml-1 shrink-0 rounded px-1.5 py-0.5 text-laya-micro font-bold uppercase {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
				{priorityLabel[card.priority] ?? card.priority}
			</span>
		</div>
	</div>

	<!-- Row 2: Platform · source ref (relaxed only — compact pulls platform inline into the footer) -->
	{#if !compact}
		<div class="mb-1.5 flex items-center gap-1.5 min-w-0">
			<span class="flex items-center gap-1.5 text-laya-micro font-semibold uppercase tracking-widest text-surface-500 shrink-0">
				<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(platformKey(card.entity_id))}"></span>
				{platform}
			</span>
			{#if card.source_context}
				<span class="text-laya-micro text-surface-500">·</span>
				<span class="truncate text-laya-micro font-medium text-surface-400">{card.source_context}</span>
			{/if}
			{#if card.source_ref}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="relative min-w-0 truncate"
					onmouseenter={() => showTooltipIfTruncated(srcRefEl, card.source_ref ?? '')}
					onmouseleave={hideTooltip}
				>
					{#if card.source_url}
						<a
							bind:this={srcRefEl}
							href={card.source_url}
							target="_blank"
							rel="noopener noreferrer"
							onclick={(e) => e.stopPropagation()}
							class="block truncate text-laya-micro font-medium text-laya-orange/80 hover:text-laya-orange transition-colors"
						>{card.source_ref}</a>
					{:else}
						<span bind:this={srcRefEl} class="block truncate text-laya-micro font-medium text-surface-400">{card.source_ref}</span>
					{/if}
				</div>
			{/if}
		</div>
	{/if}

	<!-- Row 3: Title (2-line clamp). Compact tightens leading from snug (1.375) to tight (1.25). -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="relative {compact ? 'mb-1' : 'mb-1.5'}"
		onmouseenter={() => showTooltipIfTruncated(headerEl, card.header, { checkHeight: true, maxWidth: 300 })}
		onmouseleave={hideTooltip}
	>
		<h3 bind:this={headerEl} class="line-clamp-2 text-laya-base {card.read_at ? 'font-normal text-surface-200' : 'font-bold text-surface-50'} {compact ? 'leading-tight' : 'leading-snug'}">{card.header}</h3>
	</div>

	<!-- Row 4: Summary (2-line clamp) -->
	{#if $cardDescriptions}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="relative"
			onmouseenter={() => showTooltipIfTruncated(summaryEl, card.summary, { checkHeight: true, maxWidth: 300 })}
			onmouseleave={hideTooltip}
		>
			<p bind:this={summaryEl} class="line-clamp-2 text-laya-secondary leading-relaxed text-surface-400">{card.summary}</p>
		</div>
	{/if}

	<!-- Tag chips -->
	{#if card.tags?.length}
		<div class="mt-1.5 flex flex-wrap gap-1 overflow-hidden max-h-[22px]">
			{#each (compact ? card.tags.slice(0, 3) : card.tags) as tag}
				<span
					class="{tag.is_system ? 'tag-chip-system' : 'tag-chip-user'} inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none whitespace-nowrap"
					style="--tag-color: {tag.color ?? (tag.is_system ? '#6B7280' : '#C4956B')}"
				>
					{tag.tag_name}
				</span>
			{/each}
			{#if compact && card.tags.length > 3}
				<span class="text-[10px] text-surface-500">+{card.tags.length - 3}</span>
			{/if}
		</div>
	{/if}

	<!-- Row 5a: Space identifier — own row in relaxed mode only. Compact inlines it into the footer. -->
	{#if !compact && card.space_name}
		<div class="mt-3 flex items-center gap-1 text-laya-micro text-surface-500">
			<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {card.space_color ?? '#F97316'}"></span>
			<span class="truncate">{card.space_name}</span>
		</div>
	{/if}

	<!-- Row 5b: Footer.
	     Relaxed: hairline divider above + avatar/actor + persona. Space sits on its own row above.
	     Compact: no hairline, tighter top margin, and the platform + space identifier are inlined
	     between actor and persona separated by · so all metadata lives on a single line. -->
	<div class="flex items-center gap-1.5 min-w-0 {compact ? 'mt-1.5' : (card.space_name ? 'mt-1.5 pt-2 border-t border-surface-500/30' : 'mt-3 pt-2 border-t border-surface-500/30')}">
		{#if compact && platform}
			<span class="text-laya-orange shrink-0">
				<PlatformIcon platform={platformKey(card.entity_id)} size={14} />
			</span>
		{/if}
		{#if card.actor_name}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<span class="relative flex items-center gap-1.5 min-w-0 text-laya-micro text-surface-500"
				onmouseenter={() => showTooltipIfTruncated(actorEl, card.actor_name ?? '')}
				onmouseleave={hideTooltip}
			>
				{#if !compact}
					<span
						class="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full text-laya-micro font-semibold leading-none text-white/95"
						style="background-color: {actorAvatarColor(card.actor_name)}"
						aria-hidden="true"
					>{actorInitials(card.actor_name)}</span>
				{/if}
				<span bind:this={actorEl} class="block truncate">
					{card.actor_name}
				</span>
			</span>
		{/if}
		{#if compact && card.space_name}
			<span class="flex items-center gap-1 shrink-0 text-laya-micro text-surface-500 truncate">
				<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {card.space_color ?? '#F97316'}"></span>
				<span class="truncate">{card.space_name}</span>
			</span>
		{/if}
		<span class="ml-auto shrink-0 text-laya-micro font-medium {personaColors[card.persona] ?? personaColors.ENGINEER}">{card.persona}</span>
	</div>
</div>

{#if fixedTooltip}
	<div
		use:portal
		class="pointer-events-none fixed z-[100] rounded-md border border-transparent glass-tooltip px-2 py-1 text-laya-micro font-medium break-words"
		style="top: {fixedTooltip.top}px; left: {fixedTooltip.left}px;{fixedTooltip.maxWidth ? ` max-width: ${fixedTooltip.maxWidth}px; white-space: normal;` : ' white-space: nowrap;'}"
	>
		{fixedTooltip.text}
	</div>
{/if}

{#if showDeleteConfirm}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Confirm delete"
		tabindex="-1"
		onclick={(e) => { if (e.target === e.currentTarget) showDeleteConfirm = false; }}
		onkeydown={(e) => { if (e.key === 'Escape') showDeleteConfirm = false; }}
	>
		<div class="mx-4 w-full max-w-sm rounded-xl border border-red-800/40 bg-surface-800 p-5 shadow-2xl">
			<div class="mb-3 flex items-start gap-3">
				<div class="mt-0.5 rounded-full bg-red-950/60 p-1.5">
					<svg class="h-4 w-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
					</svg>
				</div>
				<div>
					<h4 class="text-laya-base font-semibold text-surface-50">Delete card permanently?</h4>
					<p class="mt-1 text-laya-secondary leading-relaxed text-surface-400">
						All details, intelligence, workspace sessions, and related events for this card will be
						<span class="font-medium text-red-400">permanently removed</span>. This cannot be undone.
					</p>
				</div>
			</div>
			<div class="flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-laya-secondary text-surface-400 transition-colors hover:text-surface-200 disabled:opacity-50"
					onclick={(e) => { e.stopPropagation(); showDeleteConfirm = false; }}
					disabled={deleting}
				>
					Cancel
				</button>
				<button
					class="rounded-md bg-red-700 px-3 py-1.5 text-laya-secondary font-medium text-red-50 transition-colors hover:bg-red-600 disabled:opacity-50"
					onclick={deleteCard}
					disabled={deleting}
				>
					{deleting ? 'Deleting…' : 'Delete permanently'}
				</button>
			</div>
		</div>
	</div>
{/if}
