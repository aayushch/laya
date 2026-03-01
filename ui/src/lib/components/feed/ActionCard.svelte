<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { chatOpen, chatInputPreset } from '$lib/stores/chat';

	let { card, onselect }: { card: ActionCard; onselect: (card: ActionCard) => void } = $props();

	let approving = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let copied = $state(false);

	const isArchived = $derived(card.status === 'archived');

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
		executing:      'bg-blue-400 animate-pulse',
		completed:      'bg-green-500',
		failed:         'bg-red-500',
		dismissed:      'bg-surface-500',
		archived:       'bg-surface-600',
		agent_running:  'bg-violet-400 animate-pulse',
		awaiting_input: 'bg-yellow-400 animate-pulse',
		staged:         'bg-emerald-400'
	};

	const statusLabel: Record<string, string> = {
		pending:        'Pending',
		approved:       'Approved',
		executing:      'Executing',
		completed:      'Completed',
		failed:         'Failed',
		dismissed:      'Dismissed',
		archived:       'Archived',
		agent_running:  'Agent Running',
		awaiting_input: 'Input Needed',
		staged:         'Staged'
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

	const platform = $derived(
		card.entity_id
			? (platformLabel[card.entity_id.split(':')[0]] ?? card.entity_id.split(':')[0])
			: ''
	);

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
		const diff = Date.now() - new Date(dateStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	async function approve(e: Event) {
		e.stopPropagation();
		approving = true;
		try {
			await engineApi.approveCard(card.card_id);
			card.status = 'approved';
		} finally {
			approving = false;
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
			await engineApi.reopenCard(card.card_id);
			card.status = 'pending';
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
	class="group/card w-full cursor-pointer rounded-xl border bg-surface-800 p-4 text-left transition
		{isArchived
			? 'border-dashed border-surface-600 opacity-50 hover:opacity-80'
			: 'border-surface-700 hover:border-laya-orange/30'}"
	onclick={() => onselect(card)}
	onkeydown={(e) => e.key === 'Enter' && onselect(card)}
>
	<!-- Top row: source · icons · priority -->
	<div class="mb-2.5 flex items-center justify-between">
		<span class="text-[10px] font-semibold uppercase tracking-widest text-surface-500">
			{platform}
		</span>
		<div class="flex items-center gap-1">
			<!-- Copy card ID -->
			<span
				role="button"
				tabindex="0"
				onclick={copyId}
				onkeydown={(e) => e.key === 'Enter' && copyId(e)}
				title="Copy card ID"
				class="cursor-pointer rounded p-0.5 transition-colors {copied ? 'text-green-400' : 'text-surface-600 hover:text-surface-300'}"
			>
				{#if copied}
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
					</svg>
				{:else}
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
					</svg>
				{/if}
			</span>
			<!-- Chat about this card -->
			<span
				role="button"
				tabindex="0"
				onclick={chatAbout}
				onkeydown={(e) => e.key === 'Enter' && chatAbout(e)}
				title="Chat about this card"
				class="cursor-pointer rounded p-0.5 text-surface-600 transition-colors hover:text-laya-orange"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
			</span>
			<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
				{card.priority}
			</span>
		</div>
	</div>

	<!-- Title -->
	<h3 class="mb-2 text-sm font-semibold leading-snug text-surface-50">{card.header}</h3>

	<!-- Summary -->
	<p class="mb-3 line-clamp-2 text-xs leading-relaxed text-surface-400">{card.summary}</p>

	<!-- Meta row: status · time · persona · workspace -->
	<div class="mb-3 flex items-center gap-1.5">
		<span class="h-1.5 w-1.5 shrink-0 rounded-full {statusDot[card.status] ?? statusDot.pending}"></span>
		<span class="text-[10px] text-surface-400">{statusLabel[card.status] ?? card.status}</span>
		<span class="text-[10px] text-surface-600">·</span>
		<span class="text-[10px] text-surface-500">{timeAgo(card.created_at)}</span>
		<span class="text-[10px] text-surface-600">·</span>
		<span class="text-[10px] font-medium {personaColors[card.persona] ?? personaColors.ENGINEER}">
			{card.persona}
		</span>
		{#if card.has_workspace}
			<a
				href="/workspace/{card.card_id}"
				class="ml-auto rounded bg-violet-900/40 px-1.5 py-0.5 text-[10px] font-medium text-violet-300 transition-colors hover:bg-violet-900/60"
				onclick={(e) => e.stopPropagation()}
			>
				Workspace
			</a>
		{/if}
	</div>

	<!-- Actions -->
	{#if card.status === 'pending'}
		<div class="flex gap-2">
			<button
				class="rounded-lg bg-green-700/30 px-3 py-1.5 text-xs font-medium text-green-300 transition-colors hover:bg-green-700/50 disabled:opacity-50"
				onclick={approve}
				disabled={approving}
			>
				{approving ? 'Approving…' : 'Approve'}
			</button>
			<button
				class="rounded-lg bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-400 transition-colors hover:bg-surface-700 disabled:opacity-50"
				onclick={dismiss}
				disabled={dismissing}
			>
				{dismissing ? 'Dismissing…' : 'Dismiss'}
			</button>
			<button
				class="ml-auto rounded-lg px-2 py-1.5 text-xs text-surface-600 transition-colors hover:text-surface-400 disabled:opacity-50"
				onclick={archive}
				disabled={archiving}
			>
				{archiving ? '…' : 'Archive'}
			</button>
		</div>
	{:else if card.status === 'dismissed'}
		<div class="flex gap-2">
			<button
				class="rounded-lg bg-laya-orange/15 px-3 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/25 disabled:opacity-50"
				onclick={reopen}
				disabled={reopening}
			>
				{reopening ? 'Reopening…' : 'Reopen'}
			</button>
			<button
				class="rounded-lg bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
				onclick={archive}
				disabled={archiving}
			>
				{archiving ? 'Archiving…' : 'Archive'}
			</button>
		</div>
	{:else if card.status === 'archived'}
		<div class="flex gap-2">
			<button
				class="rounded-lg bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-400 transition-colors hover:bg-surface-700 disabled:opacity-50"
				onclick={reopen}
				disabled={reopening}
			>
				{reopening ? 'Unarchiving…' : 'Unarchive'}
			</button>
		</div>
	{:else if card.status === 'failed'}
		<div class="flex gap-2">
			<button
				class="rounded-lg bg-orange-700/30 px-3 py-1.5 text-xs font-medium text-orange-300 transition-colors hover:bg-orange-700/50"
				onclick={approve}
				disabled={approving}
			>
				{approving ? 'Retrying…' : 'Retry'}
			</button>
			<button
				class="ml-auto rounded-lg px-2 py-1.5 text-xs text-surface-600 transition-colors hover:text-surface-400 disabled:opacity-50"
				onclick={archive}
				disabled={archiving}
			>
				{archiving ? '…' : 'Archive'}
			</button>
		</div>
	{:else if card.status === 'approved' || card.status === 'completed'}
		<div class="flex justify-end">
			<button
				class="rounded-lg px-2 py-1.5 text-xs text-surface-600 transition-colors hover:text-surface-400 disabled:opacity-50"
				onclick={archive}
				disabled={archiving}
			>
				{archiving ? 'Archiving…' : 'Archive'}
			</button>
		</div>
	{/if}
</div>
