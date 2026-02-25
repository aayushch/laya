<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';

	let { card, onselect }: { card: ActionCard; onselect: (card: ActionCard) => void } = $props();

	let approving = $state(false);
	let dismissing = $state(false);

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH: 'bg-orange-600 text-orange-50',
		MEDIUM: 'bg-blue-600 text-blue-50',
		LOW: 'bg-surface-600 text-surface-200'
	};

	const personaColors: Record<string, string> = {
		ENGINEER: 'border-violet-500 text-violet-400',
		COMMS: 'border-emerald-500 text-emerald-400',
		OPS: 'border-amber-500 text-amber-400'
	};

	const statusDot: Record<string, string> = {
		pending: 'bg-yellow-400',
		approved: 'bg-green-400',
		executing: 'bg-blue-400 animate-pulse',
		completed: 'bg-green-500',
		failed: 'bg-red-500',
		dismissed: 'bg-surface-500',
		agent_running: 'bg-violet-400 animate-pulse',
		awaiting_input: 'bg-yellow-400 animate-pulse',
		staged: 'bg-emerald-400'
	};

	const statusLabel: Record<string, string> = {
		pending: 'Pending',
		approved: 'Approved',
		executing: 'Executing',
		completed: 'Completed',
		failed: 'Failed',
		dismissed: 'Dismissed',
		agent_running: 'Agent Running',
		awaiting_input: 'Input Needed',
		staged: 'Staged'
	};

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
</script>

<button
	class="w-full rounded-xl border border-surface-700 bg-surface-800 p-4 text-left transition-colors hover:border-surface-500 hover:bg-surface-750"
	onclick={() => onselect(card)}
>
	<!-- Top row: badges + timestamp -->
	<div class="mb-2 flex items-center gap-2">
		<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
			{card.priority}
		</span>
		<span class="rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase {personaColors[card.persona] ?? personaColors.ENGINEER}">
			{card.persona}
		</span>
		<span class="h-2 w-2 rounded-full {statusDot[card.status] ?? statusDot.pending}"></span>
		<span class="text-[10px] text-surface-400">{statusLabel[card.status] ?? card.status}</span>
		{#if card.has_workspace}
			<a
				href="/workspace/{card.card_id}"
				class="rounded bg-violet-900/40 px-1.5 py-0.5 text-[10px] font-medium text-violet-300 transition-colors hover:bg-violet-900/60"
				onclick={(e) => e.stopPropagation()}
			>
				Workspace
			</a>
		{/if}
		<span class="ml-auto text-xs text-surface-500">{timeAgo(card.created_at)}</span>
	</div>

	<!-- Header -->
	<h3 class="mb-1 text-sm font-semibold text-surface-100">{card.header}</h3>

	<!-- Summary (clamped to 2 lines) -->
	<p class="mb-3 line-clamp-2 text-xs text-surface-400">{card.summary}</p>

	<!-- Quick actions for pending cards -->
	{#if card.status === 'pending'}
		<div class="flex gap-2">
			<button
				class="rounded-lg bg-green-700/30 px-3 py-1.5 text-xs font-medium text-green-300 transition-colors hover:bg-green-700/50"
				onclick={approve}
				disabled={approving}
			>
				{approving ? 'Approving...' : 'Approve'}
			</button>
			<button
				class="rounded-lg bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-400 transition-colors hover:bg-surface-700"
				onclick={dismiss}
				disabled={dismissing}
			>
				{dismissing ? 'Dismissing...' : 'Dismiss'}
			</button>
		</div>
	{/if}
</button>
