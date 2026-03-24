<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
	import { chatOpen, chatInputPreset } from '$lib/stores/chat';
	import StatusDot from './StatusDot.svelte';

	let { card, onselect, ondelete, selectedCardId = '', hasSelection = false }: { card: ActionCard; onselect: (card: ActionCard) => void; ondelete?: (cardId: string) => void; selectedCardId?: string; hasSelection?: boolean } = $props();

	const isSelected = $derived(card.card_id === selectedCardId);

	let markingDone = $state(false);
	let approvingAgent = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let copied = $state(false);
	let showDeleteConfirm = $state(false);
	let deleting = $state(false);
	let actionMenuOpen = $state(false);

	// Close action overflow menu on outside click — swallow the event so it
	// doesn't propagate to the card and trigger a selection.
	$effect(() => {
		if (!actionMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!target.closest('.action-overflow-menu')) {
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
		OPS:      'text-amber-400'
	};

	const statusDot: Record<string, string> = {
		pending:            'bg-yellow-400 animate-pulse',
		ready:              'bg-amber-400',
		requires_approval:  'bg-violet-400',
		agent_running:      'bg-violet-400 animate-pulse',
		awaiting_input:     'bg-yellow-400 animate-pulse',
		done:               'bg-green-500',
		failed:             'bg-red-500',
		dismissed:          'bg-surface-500',
		archived:           'bg-surface-600'
	};

	const statusLabel: Record<string, string> = {
		pending:            'Processing',
		ready:              'Ready',
		requires_approval:  'Needs Approval',
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

	const statusCardStyle: Record<string, string> = {
		pending:            'bg-amber-950/55  border-amber-800/30  hover:border-amber-700/45  card-pulse-amber',
		ready:              'bg-amber-950/55  border-amber-800/30  hover:border-amber-700/45',
		requires_approval:  'bg-violet-950/55 border-violet-800/25 hover:border-violet-700/40',
		agent_running:      'bg-violet-950/55 border-violet-800/25 hover:border-violet-700/40 card-pulse-violet',
		awaiting_input:     'bg-amber-950/55  border-amber-800/30  hover:border-amber-700/45  card-pulse-amber',
		done:               'bg-emerald-950/50 border-emerald-800/20 hover:border-emerald-700/35',
		failed:             'bg-rose-950/60   border-rose-800/35   hover:border-rose-700/50',
		dismissed:          'bg-surface-800/40 border-surface-700/25 hover:border-surface-600/40 opacity-50 hover:opacity-75',
	};

	const baseCardStyle = $derived(
		isArchived
			? 'bg-surface-900/60 border-dashed border-surface-700/50 opacity-50 hover:opacity-80'
			: (statusCardStyle[card.status] ?? 'bg-surface-800 border-surface-700 hover:border-laya-orange/30')
	);

	const isDimmed = $derived(!isSelected && hasSelection && !isArchived);

	const cardStyle = $derived(
		`${baseCardStyle}${isDimmed ? ' opacity-45 hover:opacity-70' : ''}`
	);

	const platform = $derived(
		card.entity_id
			? (platformLabel[card.entity_id.split(':')[0]] ?? card.entity_id.split(':')[0])
			: ''
	);

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

	async function markDone(e: Event) {
		e.stopPropagation();
		markingDone = true;
		try {
			await engineApi.markCardDone(card.card_id);
			card.status = 'done';
		} finally {
			markingDone = false;
		}
	}

	async function approveAgent(e: Event) {
		e.stopPropagation();
		approvingAgent = true;
		try {
			await engineApi.approveAgent(card.card_id);
			card.status = 'agent_running';
		} finally {
			approvingAgent = false;
		}
	}

	async function dismiss(e: Event) {
		e.stopPropagation();
		dismissing = true;
		try {
			await engineApi.dismissCard(card.card_id);
			card.status = 'dismissed';
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

	async function copyId(e: Event) {
		e.stopPropagation();
		await navigator.clipboard.writeText(card.card_id);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	function deleteCard(e: Event) {
		e.stopPropagation();
		showDeleteConfirm = false;
		ondelete?.(card.card_id);
		engineApi.deleteCard(card.card_id).catch(() => {});
	}

	function chatAbout(e: Event) {
		e.stopPropagation();
		const lines = [
			`I'd like to discuss this action card (ID: ${card.card_id}):`,
			``,
			`**Title:** ${card.header}`,
			`**Summary:** ${card.summary}`,
			`**Priority:** ${card.priority} · **Status:** ${card.status} · **Persona:** ${card.persona} · **Category:** ${card.category}`,
		];
		if (card.intelligence && card.intelligence.length > 0) {
			lines.push(``, `**Intelligence:**`);
			card.intelligence.forEach((p) => lines.push(`- ${p}`));
		}
		chatInputPreset.set(lines.join('\n'));
		chatOpen.set(true);
	}
</script>

<div
	role="button"
	tabindex="0"
	data-card-id={card.card_id}
	class="group/card flex h-[200px] w-full cursor-pointer flex-col rounded-xl border px-4 pb-2 pt-3 text-left transition-colors {cardStyle}"
	onclick={() => onselect(card)}
	onkeydown={(e) => e.key === 'Enter' && onselect(card)}
>
	<!-- Row 1: Action icons (left) + utility icons (right) -->
	<div class="mb-2 flex items-center justify-between">
		<!-- Action icons -->
		<div class="flex items-center gap-1">
			{#if card.status === 'ready'}
				<!-- Mark as Done -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Mark as Done</span>
				</div>
				<!-- Dismiss -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Dismiss</span>
				</div>
				{#if card.has_workspace}
					<!-- Overflow: Archive + Workspace -->
					<div class="action-overflow-menu relative">
						<button
							class="flex h-6 w-6 items-center justify-center rounded-md text-surface-500 transition-all hover:bg-surface-700/50 hover:text-surface-300"
							onclick={(e) => { e.stopPropagation(); actionMenuOpen = !actionMenuOpen; }}
							aria-label="More actions"
						>
							<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
								<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
							</svg>
						</button>
						{#if actionMenuOpen}
							<div class="absolute left-0 top-full z-50 mt-1 w-40 rounded-lg border border-surface-600 bg-surface-800 p-1 shadow-xl shadow-black/30" role="menu">
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400 disabled:opacity-40"
									role="menuitem"
									onclick={(e) => { actionMenuOpen = false; archive(e); }}
									disabled={archiving}
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
									Archive
								</button>
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-violet-400"
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
					<div class="group/act relative">
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
						<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Archive</span>
					</div>
				{/if}
			{:else if card.status === 'requires_approval'}
				<!-- Done -->
				<div class="group/act relative">
					<button
						aria-label="Mark as Done"
						class="flex h-6 w-6 items-center justify-center rounded-md text-green-400/60 transition-all hover:bg-green-500/15 hover:text-green-400 disabled:opacity-40"
						onclick={markDone}
						disabled={markingDone}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
					</button>
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Done</span>
				</div>
				<!-- Approve Agent -->
				<div class="group/act relative">
					<button
						aria-label="Approve Agent"
						class="flex h-6 w-6 items-center justify-center rounded-md text-violet-400/60 transition-all hover:bg-violet-500/15 hover:text-violet-400 disabled:opacity-40"
						onclick={approveAgent}
						disabled={approvingAgent}
					>
						<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
							<path d="M8 5v14l11-7z" />
						</svg>
					</button>
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-violet-500/30 bg-surface-800 px-2 py-1 text-[10px] font-medium text-violet-400 opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Approve Agent</span>
				</div>
				<!-- Overflow: Dismiss + Archive -->
				<div class="action-overflow-menu relative">
					<button
						class="flex h-6 w-6 items-center justify-center rounded-md text-surface-500 transition-all hover:bg-surface-700/50 hover:text-surface-300"
						onclick={(e) => { e.stopPropagation(); actionMenuOpen = !actionMenuOpen; }}
						aria-label="More actions"
					>
						<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
							<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
						</svg>
					</button>
					{#if actionMenuOpen}
						<div class="absolute left-0 top-full z-50 mt-1 w-36 rounded-lg border border-surface-600 bg-surface-800 p-1 shadow-xl shadow-black/30" role="menu">
							<button
								class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-surface-200 disabled:opacity-40"
								role="menuitem"
								onclick={(e) => { actionMenuOpen = false; dismiss(e); }}
								disabled={dismissing}
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" d="M6 18L18 6M6 6l12 12" /></svg>
								Dismiss
							</button>
							<button
								class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-red-400 disabled:opacity-40"
								role="menuitem"
								onclick={(e) => { actionMenuOpen = false; archive(e); }}
								disabled={archiving}
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" /></svg>
								Archive
							</button>
							{#if card.has_workspace}
								<button
									class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700 hover:text-violet-400"
									role="menuitem"
									onclick={(e) => { e.stopPropagation(); actionMenuOpen = false; goto(`/workspace/${card.card_id}`); }}
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
									Workspace
								</button>
							{/if}
						</div>
					{/if}
				</div>
			{:else if card.status === 'dismissed'}
				<!-- Reopen -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Reopen</span>
				</div>
				<!-- Archive -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Archive</span>
				</div>
			{:else if card.status === 'archived'}
				<!-- Unarchive -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Unarchive</span>
				</div>
				<!-- Delete -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-red-500/30 bg-surface-800 px-2 py-1 text-[10px] font-medium text-red-400 opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Delete</span>
				</div>
			{:else if card.status === 'done'}
				<!-- Reopen -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Reopen</span>
				</div>
				<!-- Archive -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Archive</span>
				</div>
			{:else if card.status === 'failed'}
				<!-- Retry -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Retry</span>
				</div>
				<!-- Archive -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Archive</span>
				</div>
			{:else if card.status === 'awaiting_input'}
				<!-- Archive -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Archive</span>
				</div>
			{/if}
			{#if card.has_workspace && card.status !== 'ready' && card.status !== 'requires_approval'}
				<!-- Open Workspace — shown as standalone button only for statuses with ≤2 actions -->
				<div class="group/act relative">
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
					<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-violet-500/30 bg-surface-800 px-2 py-1 text-[10px] font-medium text-violet-400 opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Open Workspace</span>
				</div>
			{/if}
		</div>

		<!-- Right: utility icons (appear on hover) + priority chip -->
		<div class="flex items-center gap-0.5 min-w-0">
			<!-- Copy card ID -->
			<div class="group/act relative">
				<span
					role="button"
					tabindex="0"
					onclick={copyId}
					onkeydown={(e) => e.key === 'Enter' && copyId(e)}
					class="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md transition-all opacity-0 group-hover/card:opacity-100 {copied ? 'text-green-400' : 'text-surface-600 hover:bg-surface-700/50 hover:text-surface-300'}"
				>
					{#if copied}
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
						</svg>
					{:else}
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
						</svg>
					{/if}
				</span>
				<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">{copied ? 'Copied!' : 'Copy ID'}</span>
			</div>
			<!-- Chat -->
			<div class="group/act relative">
				<span
					role="button"
					tabindex="0"
					onclick={chatAbout}
					onkeydown={(e) => e.key === 'Enter' && chatAbout(e)}
					class="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-surface-600 transition-all hover:bg-surface-700/50 hover:text-laya-orange opacity-0 group-hover/card:opacity-100"
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
					</svg>
				</span>
				<span class="pointer-events-none absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Chat</span>
			</div>
			<!-- Status indicator -->
			<span class="ml-1 flex items-center gap-1 min-w-0 overflow-hidden">
				<StatusDot status={card.status} size="md" />
				<span class="text-[11px] text-surface-400 truncate">{statusLabel[card.status] ?? card.status}</span>
			</span>
			<!-- Priority chip -->
			<span class="ml-1 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
				{priorityLabel[card.priority] ?? card.priority}
			</span>
		</div>
	</div>

	<!-- Row 2: Platform · source ref -->
	<div class="mb-1.5 flex items-center gap-1.5 min-w-0">
		<span class="text-[10px] font-semibold uppercase tracking-widest text-surface-500 shrink-0">
			{platform}
		</span>
		{#if card.source_ref}
			{#if card.source_url}
				<a
					href={card.source_url}
					target="_blank"
					rel="noopener noreferrer"
					onclick={(e) => e.stopPropagation()}
					class="truncate text-[10px] font-medium text-laya-orange/80 hover:text-laya-orange transition-colors"
					title={card.source_ref}
				>{card.source_ref}</a>
			{:else}
				<span class="truncate text-[10px] font-medium text-surface-400" title={card.source_ref}>{card.source_ref}</span>
			{/if}
		{/if}
	</div>

	<!-- Row 3: Title (2-line clamp) -->
	<h3 class="mb-1.5 line-clamp-2 text-sm font-semibold leading-snug text-surface-50" title={card.header}>{card.header}</h3>

	<!-- Row 4: Summary (2-line clamp) -->
	<p class="line-clamp-2 text-xs leading-relaxed text-surface-400" title={card.summary}>{card.summary}</p>

	<!-- Spacer — absorbs leftover height so footer stays pinned to bottom -->
	<div class="flex-1 min-h-1"></div>

	<!-- Row 5: Footer — space · actor name (left) · persona · category · workspace · time (right) -->
	<div class="flex items-center gap-1.5 min-w-0">
		{#if card.space_name}
			<span class="flex items-center gap-1 shrink-0 text-[10px] text-surface-500" title="Space: {card.space_name}">
				<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {card.space_color ?? '#F97316'}"></span>
				{card.space_name}
			</span>
			{#if card.actor_name}
				<span class="text-[10px] text-surface-600">·</span>
			{/if}
		{/if}
		{#if card.actor_name}
			<span class="truncate text-[10px] text-surface-500" title={card.actor_name}>{card.actor_name}</span>
		{/if}
		<span class="ml-auto shrink-0 text-[10px] font-medium {personaColors[card.persona] ?? personaColors.ENGINEER}">{card.persona}</span>
		<span class="shrink-0 whitespace-nowrap text-[10px] text-surface-500">{timeAgo(card.created_at)}</span>
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
					<p class="mt-1 text-xs leading-relaxed text-surface-400">
						All details, intelligence, workspace sessions, and related events for this card will be
						<span class="font-medium text-red-400">permanently removed</span>. This cannot be undone.
					</p>
				</div>
			</div>
			<div class="flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200 disabled:opacity-50"
					onclick={(e) => { e.stopPropagation(); showDeleteConfirm = false; }}
					disabled={deleting}
				>
					Cancel
				</button>
				<button
					class="rounded-md bg-red-700 px-3 py-1.5 text-xs font-medium text-red-50 transition-colors hover:bg-red-600 disabled:opacity-50"
					onclick={deleteCard}
					disabled={deleting}
				>
					{deleting ? 'Deleting…' : 'Delete permanently'}
				</button>
			</div>
		</div>
	</div>
{/if}
