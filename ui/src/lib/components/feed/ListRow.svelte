<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
	import { cardColors } from '$lib/stores/cardColors';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import StatusDot from './StatusDot.svelte';
	import { platformDotColor, platformKey } from '$lib/utils/cardVisuals';

	let {
		card,
		onselect,
		ondelete,
		selectedCardId = '',
		indented = false,
		bulkSelected = false,
		onbulktoggle,
		hasSelection = false,
		lastViewedCardId = ''
	}: {
		card: ActionCard;
		onselect: (card: ActionCard) => void;
		ondelete?: (cardId: string) => void;
		selectedCardId?: string;
		indented?: boolean;
		bulkSelected?: boolean;
		onbulktoggle?: (cardId: string, event: MouseEvent) => void;
		hasSelection?: boolean;
		lastViewedCardId?: string;
	} = $props();

	const isSelected = $derived(card.card_id === selectedCardId);
	const isLastViewed = $derived(!isSelected && !hasSelection && card.card_id === lastViewedCardId);

	let bookmarking = $state(false);
	let markingDone = $state(false);
	let approvingAgent = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let showDeleteConfirm = $state(false);
	let deleting = $state(false);
	let actorEl: HTMLSpanElement | undefined = $state();
	let subjectEl: HTMLSpanElement | undefined = $state();
	let fixedTooltip = $state<{ text: string; top: number; left: number; maxWidth?: number } | null>(null);

	function showTooltip(el: HTMLElement, text: string, opts?: { maxWidth?: number }) {
		const rect = el.getBoundingClientRect();
		fixedTooltip = { text, top: rect.bottom + 4, left: rect.left, maxWidth: opts?.maxWidth };
	}
	function showTooltipIfTruncated(el: HTMLElement | undefined, text: string, opts?: { maxWidth?: number }) {
		if (!el) return;
		if (el.scrollWidth <= el.clientWidth) { fixedTooltip = null; return; }
		showTooltip(el, text, opts);
	}
	function hideTooltip() { fixedTooltip = null; }

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH: 'bg-rose-500/25 text-rose-300',
		MEDIUM: 'bg-amber-500/20 text-amber-300',
		LOW: 'bg-surface-700/40 text-surface-400'
	};
	const priorityLabel: Record<string, string> = {
		CRITICAL: 'CRIT',
		HIGH: 'HIGH',
		MEDIUM: 'MED',
		LOW: 'LOW'
	};
	const personaColors: Record<string, string> = {
		ENGINEER: 'text-violet-400 bg-violet-500/10 border-violet-500/20',
		COMMS: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
		OPS: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
		SALES: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
		HR: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
		FINANCE: 'text-teal-400 bg-teal-500/10 border-teal-500/20'
	};
	const statusDot: Record<string, string> = {
		pending: 'bg-yellow-400 animate-pulse',
		ready: 'bg-amber-400',
		requires_approval: 'bg-sky-400',
		agent_running: 'bg-violet-400 animate-pulse',
		awaiting_input: 'bg-yellow-400 animate-pulse',
		done: 'bg-green-500',
		failed: 'bg-red-500',
		dismissed: 'bg-surface-500',
		archived: 'bg-surface-600'
	};
	const solidRowStyle: Record<string, string> = {
		pending:            'bg-amber-950/55',
		ready:              'bg-amber-950/55',
		requires_approval:  'bg-sky-950/55',
		agent_running:      'bg-violet-950/55',
		awaiting_input:     'bg-amber-950/55',
		done:               'bg-emerald-950/50',
		failed:             'bg-rose-950/60',
		dismissed:          'bg-surface-800/40',
		archived:           'bg-surface-900/60',
	};
	const glassRowStyle: Record<string, string> = {
		pending:            'glass-card-flat bg-amber-950/45',
		ready:              'glass-card-flat bg-amber-950/45',
		requires_approval:  'glass-card-flat bg-sky-950/45',
		agent_running:      'glass-card-flat bg-violet-950/45',
		awaiting_input:     'glass-card-flat bg-amber-950/45',
		done:               'glass-card-flat bg-emerald-950/40',
		failed:             'glass-card-flat bg-rose-950/50',
		dismissed:          'glass-card-flat bg-surface-800/30',
		archived:           'glass-card-flat bg-surface-900/35',
	};
	const statusRowStyle = $derived($glassTheme ? glassRowStyle : solidRowStyle);
	const statusLabel: Record<string, string> = {
		pending: 'Processing',
		ready: 'Ready',
		requires_approval: 'Approval',
		agent_running: 'Running',
		awaiting_input: 'Input',
		done: 'Done',
		failed: 'Failed',
		dismissed: 'Dismissed',
		archived: 'Archived'
	};
	const platformLabel: Record<string, string> = {
		jira: 'Jira',
		gmail: 'Gmail',
		slack: 'Slack',
		bitbucket: 'Bitbucket',
		calendar: 'Calendar',
		github: 'GitHub',
		laya: 'Laya'
	};

	const platform = $derived(
		card.entity_id
			? (platformLabel[card.entity_id.split(':')[0]] ?? card.entity_id.split(':')[0])
			: ''
	);

	const isArchived = $derived(card.status === 'archived');
	const isDimmed = $derived(!isSelected && hasSelection && !isArchived);

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
		const utcStr =
			dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const diff = Date.now() - new Date(utcStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	async function markDone(e: Event) {
		e.stopPropagation();
		markingDone = true;
		try { await engineApi.markCardDone(card.card_id); card.status = 'done'; } finally { markingDone = false; }
	}
	async function approveAgent(e: Event) {
		e.stopPropagation();
		approvingAgent = true;
		try { await engineApi.approveAgent(card.card_id); card.status = 'agent_running'; } finally { approvingAgent = false; }
	}
	async function dismiss(e: Event) {
		e.stopPropagation();
		dismissing = true;
		try { await engineApi.dismissCard(card.card_id); card.status = 'dismissed'; } finally { dismissing = false; }
	}
	async function archive(e: Event) {
		e.stopPropagation();
		archiving = true;
		try { await engineApi.archiveCard(card.card_id); card.status = 'archived'; } finally { archiving = false; }
	}
	async function reopen(e: Event) {
		e.stopPropagation();
		reopening = true;
		try { await engineApi.reopenCard(card.card_id); card.status = 'pending'; } finally { reopening = false; }
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
	function deleteCard(e: Event) {
		e.stopPropagation();
		showDeleteConfirm = false;
		ondelete?.(card.card_id);
		engineApi.deleteCard(card.card_id).catch(() => {});
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="flex items-center {indented ? 'pl-6' : ''} {onbulktoggle ? 'gap-1.5' : ''}">
	{#if onbulktoggle}
		<div class="w-5 shrink-0 flex items-center justify-center">
			<button
				class="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors
					{bulkSelected
						? 'bg-laya-orange border-laya-orange'
						: 'border-surface-500 hover:border-surface-300 bg-transparent'}"
				onclick={(e) => { e.stopPropagation(); onbulktoggle(card.card_id, e); }}
				aria-label="{bulkSelected ? 'Deselect' : 'Select'} card"
			>
				{#if bulkSelected}
					<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
					</svg>
				{/if}
			</button>
		</div>
	{/if}

	<div
		data-card-id={card.card_id}
		data-status={$glassTheme && $cardColors && !isArchived ? card.status : undefined}
		class="group/row list-row-hover relative flex flex-1 min-w-0 items-center rounded-lg transition-colors hover:z-20
			border border-transparent {$cardColors ? (statusRowStyle[card.status] ?? '') : ''}
			{isArchived ? 'opacity-50 hover:opacity-75' : isDimmed ? ($glassTheme ? 'glass-dim' : 'opacity-45 hover:opacity-70') : ''}
			{isLastViewed ? ($cardColors ? 'card-last-viewed card-last-viewed--compact' : 'card-last-viewed-highlight') : ''}"
		style="{isLastViewed ? '--corner-radius: 0.5rem' : ''}"
	>
		{#if isLastViewed}<div class="card-corner-bottom"></div>{/if}

	<div
		class="flex flex-1 min-w-0 items-center px-3 py-1.5 text-left cursor-pointer"
		onclick={() => onselect(card)}
		onkeydown={(e) => e.key === 'Enter' && onselect(card)}
		role="button"
		tabindex="0"
	>
		{#if isLastViewed}<div class="card-corner-bottom"></div>{/if}
		<!-- Bookmark — replaces chevron spacer -->
		<button
			onclick={toggleBookmark}
			aria-label={card.bookmarked_at ? 'Remove bookmark' : 'Bookmark card'}
			class="w-5 shrink-0 flex items-center justify-center transition-colors {card.bookmarked_at ? 'text-laya-orange' : 'text-surface-600 hover:text-laya-orange'}"
			disabled={bookmarking}
		>
			<svg class="h-3.5 w-3.5" fill={card.bookmarked_at ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
			</svg>
		</button>

		<!-- Source — fixed width, brand-colored dot prefix for at-a-glance scanning -->
	<span class="w-[76px] shrink-0 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-surface-500 truncate" title={platform}>
		<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(platformKey(card.entity_id))}"></span>
		<span class="truncate">{platform}</span>
	</span>

	<!-- Icon spacer — matches ListGroup linked icon slot -->
	<span class="w-3 shrink-0 ml-2"></span>

	<!-- Actor — fixed width, always present for alignment -->
	<span class="w-[90px] shrink-0 ml-1"
		onmouseenter={() => showTooltipIfTruncated(actorEl, card.actor_name ?? '')}
		onmouseleave={hideTooltip}
	>
		<span bind:this={actorEl} class="block truncate text-xs text-surface-400">
			{card.actor_name ?? ''}
		</span>
	</span>

	<!-- Subject (header) — takes remaining space -->
	<span class="min-w-0 flex-1 ml-2"
		onmouseenter={() => showTooltipIfTruncated(subjectEl, card.header, { maxWidth: 320 })}
		onmouseleave={hideTooltip}
	>
		<span bind:this={subjectEl} class="block truncate text-xs font-medium text-surface-200">
			{card.header}
		</span>
	</span>

	<!-- Status — fixed width / Approve button for requires_approval -->
	{#if card.status === 'requires_approval'}
		<span class="w-[70px] shrink-0 flex items-center justify-center ml-2">
			<button
				class="flex items-center gap-1 rounded-full bg-violet-500/20 px-2 py-0.5 text-[10px] font-medium text-violet-400 transition-colors hover:bg-violet-500/30 disabled:opacity-40"
				onclick={approveAgent}
				disabled={approvingAgent}
			>
				<svg class="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
				{approvingAgent ? '...' : 'Approve'}
			</button>
		</span>
	{:else}
		<span class="w-[70px] shrink-0 flex items-center gap-1 ml-2">
			<StatusDot status={card.status} size="md" errorMessage={card.last_error} />
			<span class="text-[11px] text-surface-500 whitespace-nowrap truncate" title={card.status === 'failed' && card.last_error ? card.last_error : ''}>{statusLabel[card.status] ?? card.status}</span>
		</span>
	{/if}

	<!-- Action buttons — fixed width slot (visible on hover) -->
	<div class="w-[68px] shrink-0 flex items-center justify-end gap-0.5 opacity-0 group-hover/row:opacity-100 transition-opacity">
		{#if card.status === 'ready'}
			<button aria-label="Mark as Done" class="h-5 w-5 flex items-center justify-center rounded text-green-400/60 hover:bg-green-500/15 hover:text-green-400 disabled:opacity-40" onclick={markDone} disabled={markingDone} onmouseenter={(e) => showTooltip(e.currentTarget, 'Done')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
			</button>
			<button aria-label="Dismiss" class="h-5 w-5 flex items-center justify-center rounded text-surface-500 hover:bg-surface-500/15 hover:text-surface-300 disabled:opacity-40" onclick={dismiss} disabled={dismissing} onmouseenter={(e) => showTooltip(e.currentTarget, 'Dismiss')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" d="M6 18L18 6M6 6l12 12" /></svg>
			</button>
			<button aria-label="Archive" class="h-5 w-5 flex items-center justify-center rounded text-red-400/60 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40" onclick={archive} disabled={archiving} onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
			</button>
		{:else if card.status === 'requires_approval'}
			<button aria-label="Mark as Done" class="h-5 w-5 flex items-center justify-center rounded text-green-400/60 hover:bg-green-500/15 hover:text-green-400 disabled:opacity-40" onclick={markDone} disabled={markingDone} onmouseenter={(e) => showTooltip(e.currentTarget, 'Done')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
			</button>
			<button aria-label="Dismiss" class="h-5 w-5 flex items-center justify-center rounded text-surface-500 hover:bg-surface-500/15 hover:text-surface-300 disabled:opacity-40" onclick={dismiss} disabled={dismissing} onmouseenter={(e) => showTooltip(e.currentTarget, 'Dismiss')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" d="M6 18L18 6M6 6l12 12" /></svg>
			</button>
			<button aria-label="Archive" class="h-5 w-5 flex items-center justify-center rounded text-red-400/60 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40" onclick={archive} disabled={archiving} onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
			</button>
		{:else if card.status === 'dismissed' || card.status === 'done'}
			<button aria-label="Reopen" class="h-5 w-5 flex items-center justify-center rounded text-laya-orange/60 hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40" onclick={reopen} disabled={reopening} onmouseenter={(e) => showTooltip(e.currentTarget, 'Reopen')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4" /></svg>
			</button>
			<button aria-label="Archive" class="h-5 w-5 flex items-center justify-center rounded text-red-400/60 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40" onclick={archive} disabled={archiving} onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
			</button>
		{:else if card.status === 'archived'}
			<button aria-label="Unarchive" class="h-5 w-5 flex items-center justify-center rounded text-laya-orange/60 hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40" onclick={reopen} disabled={reopening} onmouseenter={(e) => showTooltip(e.currentTarget, 'Unarchive')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4" /></svg>
			</button>
			<button aria-label="Delete" class="h-5 w-5 flex items-center justify-center rounded text-red-400/60 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40" onclick={(e) => { e.stopPropagation(); showDeleteConfirm = true; }} disabled={deleting} onmouseenter={(e) => showTooltip(e.currentTarget, 'Delete')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
			</button>
		{:else if card.status === 'failed'}
			<button aria-label="Retry" class="h-5 w-5 flex items-center justify-center rounded text-laya-orange/60 hover:bg-laya-orange/15 hover:text-laya-orange disabled:opacity-40" onclick={reopen} disabled={reopening} onmouseenter={(e) => showTooltip(e.currentTarget, 'Retry')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M1 4v6h6" /><path stroke-linecap="round" stroke-linejoin="round" d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></svg>
			</button>
			<button aria-label="Archive" class="h-5 w-5 flex items-center justify-center rounded text-surface-500 hover:bg-surface-500/15 hover:text-surface-300 disabled:opacity-40" onclick={archive} disabled={archiving} onmouseenter={(e) => showTooltip(e.currentTarget, 'Archive')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
			</button>
		{/if}
		{#if card.has_workspace}
			<a href="/workspace/{card.card_id}" aria-label="Workspace" class="h-5 w-5 flex items-center justify-center rounded text-violet-400/60 hover:bg-violet-500/15 hover:text-violet-400" onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/workspace/${card.card_id}`); }} onmouseenter={(e) => showTooltip(e.currentTarget, 'Workspace')} onmouseleave={hideTooltip}>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
			</a>
		{/if}
	</div>

	<!-- Persona badge — fixed width -->
	<span class="w-[62px] shrink-0 text-center rounded border px-1 py-0.5 text-[9px] font-bold uppercase ml-1 {personaColors[card.persona] ?? personaColors.ENGINEER}">
		{card.persona}
	</span>

	<!-- Priority badge — fixed width -->
	<span class="w-[36px] shrink-0 text-center rounded px-1 py-0.5 text-[9px] font-bold uppercase ml-1 {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
		{priorityLabel[card.priority] ?? card.priority}
	</span>

	<!-- Space badge — fixed width -->
	<span class="w-[72px] shrink-0 flex items-center gap-1 ml-1 truncate">
		{#if card.space_name}
			<span class="inline-flex items-center gap-1 rounded border border-surface-700 bg-surface-800/60 px-1.5 py-0.5 text-[9px] text-surface-400 truncate">
				<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {card.space_color ?? '#F97316'}"></span>
				<span class="truncate">{card.space_name}</span>
			</span>
		{/if}
	</span>

	<!-- Time — fixed width -->
	<span class="w-[52px] shrink-0 text-right text-[10px] text-surface-500 whitespace-nowrap">{timeAgo(card.created_at)}</span>
	</div>
</div>
</div>

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
					<h4 class="text-sm font-semibold text-surface-50">Delete card permanently?</h4>
					<p class="mt-1 text-xs leading-relaxed text-surface-400">This cannot be undone.</p>
				</div>
			</div>
			<div class="flex justify-end gap-2">
				<button class="rounded-md px-3 py-1.5 text-xs text-surface-400 hover:text-surface-200" onclick={(e) => { e.stopPropagation(); showDeleteConfirm = false; }}>Cancel</button>
				<button class="rounded-md bg-red-700 px-3 py-1.5 text-xs font-medium text-red-50 hover:bg-red-600" onclick={deleteCard} disabled={deleting}>{deleting ? 'Deleting...' : 'Delete'}</button>
			</div>
		</div>
	</div>
{/if}

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {fixedTooltip.top}px; left: {fixedTooltip.left}px;{fixedTooltip.maxWidth ? ` max-width: ${fixedTooltip.maxWidth}px; white-space: normal;` : ' white-space: nowrap;'}"
	>
		{fixedTooltip.text}
	</span>
{/if}
