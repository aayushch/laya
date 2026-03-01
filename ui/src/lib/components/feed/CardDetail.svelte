<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { chatOpen, chatInputPreset } from '$lib/stores/chat';

	let {
		card,
		onclose
	}: { card: ActionCard; onclose: () => void } = $props();

	let approving = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let copied = $state(false);
	let dismissReason = $state('');
	let showDismissInput = $state(false);
	let executingActionId = $state<string | null>(null);
	let executeError = $state<string | null>(null);

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

	const outputTypeLabels: Record<string, string> = {
		draft_reply: 'Draft Reply',
		code_fix: 'Code Fix',
		briefing: 'Briefing',
		summary: 'Summary'
	};

	const terminalStatuses = new Set(['completed', 'failed', 'dismissed', 'archived']);
	const actionableStatuses = new Set(['pending', 'approved', 'staged', 'agent_running', 'awaiting_input']);
	let isActionable = $derived(actionableStatuses.has(card.status));
	let isTerminal = $derived(terminalStatuses.has(card.status));

	const statusColors: Record<string, string> = {
		pending: 'text-yellow-400',
		approved: 'text-green-400',
		executing: 'text-blue-400',
		completed: 'text-green-500',
		failed: 'text-red-500',
		dismissed: 'text-surface-500',
		archived: 'text-surface-600',
		agent_running: 'text-violet-400',
		awaiting_input: 'text-yellow-400',
		staged: 'text-emerald-400'
	};

	async function executeAction(actionId: string) {
		executingActionId = actionId;
		executeError = null;
		try {
			const result = await engineApi.executeAction(card.card_id, actionId);
			card.status = result.status as ActionCard['status'];
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Execution failed';
		} finally {
			executingActionId = null;
		}
	}

	async function approve() {
		approving = true;
		try {
			await engineApi.approveCard(card.card_id);
			card.status = 'approved';
		} finally {
			approving = false;
		}
	}

	async function dismiss() {
		dismissing = true;
		try {
			await engineApi.dismissCard(card.card_id, dismissReason || undefined);
			card.status = 'dismissed';
			showDismissInput = false;
		} finally {
			dismissing = false;
		}
	}

	async function archive() {
		archiving = true;
		try {
			await engineApi.archiveCard(card.card_id);
			card.status = 'archived';
		} finally {
			archiving = false;
		}
	}

	async function reopen() {
		reopening = true;
		try {
			await engineApi.reopenCard(card.card_id);
			card.status = 'pending';
		} finally {
			reopening = false;
		}
	}

	async function copyId() {
		await navigator.clipboard.writeText(card.card_id);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	function chatAbout() {
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
		if (card.staged_output) {
			lines.push(``, `**Staged Output (${card.staged_output.type}):**`, card.staged_output.content);
		}
		chatInputPreset.set(lines.join('\n'));
		chatOpen.set(true);
	}
</script>

<div class="flex h-full flex-col overflow-hidden rounded-xl border border-surface-700 bg-surface-800">
	<!-- Header bar -->
	<div class="flex items-center justify-between border-b border-surface-700 px-5 py-4">
		<div class="flex items-center gap-2">
			<span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase {priorityColors[card.priority] ?? priorityColors.MEDIUM}">
				{card.priority}
			</span>
			<span class="rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase {personaColors[card.persona] ?? personaColors.ENGINEER}">
				{card.persona}
			</span>
			{#if card.privacy_tier === 3}
				<span class="rounded bg-red-900/50 px-1.5 py-0.5 text-[10px] font-medium text-red-300">
					CONFIDENTIAL
				</span>
			{/if}
		</div>
		<div class="flex items-center gap-1">
			<!-- Copy card ID -->
			<button
				onclick={copyId}
				title="Copy card ID"
				class="rounded p-1.5 transition-colors {copied ? 'text-green-400' : 'text-surface-500 hover:text-surface-200'}"
			>
				{#if copied}
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
					</svg>
				{:else}
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
					</svg>
				{/if}
			</button>
			<!-- Chat about this card -->
			<button
				onclick={chatAbout}
				title="Chat about this card"
				class="rounded p-1.5 text-surface-500 transition-colors hover:text-laya-orange"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
			</button>
			<button aria-label="Close" class="rounded p-1.5 text-surface-400 transition-colors hover:text-surface-100" onclick={onclose}>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Scrollable content -->
	<div class="flex-1 overflow-y-auto px-5 py-4">
		<!-- Header + summary -->
		<h2 class="mb-2 text-lg font-semibold text-surface-50">{card.header}</h2>
		<p class="mb-5 text-sm text-surface-300">{card.summary}</p>

		<!-- Intelligence report -->
		{#if card.intelligence && card.intelligence.length > 0}
			<div class="mb-5">
				<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Intelligence Report</h3>
				<ul class="space-y-1.5">
					{#each card.intelligence as point}
						<li class="flex items-start gap-2 text-sm text-surface-300">
							<span class="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-surface-500"></span>
							{point}
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<!-- Staged output -->
		{#if card.staged_output}
			<div class="mb-5">
				<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">
					{outputTypeLabels[card.staged_output.type] ?? 'Output'}
				</h3>
				{#if card.staged_output.type === 'code_fix'}
					<pre class="overflow-x-auto rounded-lg bg-surface-900 p-3 text-xs text-surface-200">{card.staged_output.content}</pre>
				{:else}
					<div class="rounded-lg border border-surface-700 bg-surface-900/50 p-3 text-sm text-surface-200 whitespace-pre-wrap">
						{card.staged_output.content}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Suggested actions -->
		{#if card.suggested_actions && card.suggested_actions.length > 0}
			<div class="mb-5">
				<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Suggested Actions</h3>
				<div class="flex flex-wrap gap-2">
					{#each card.suggested_actions as action}
						<button
							class="rounded-lg border border-surface-600 bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-200 transition-colors hover:bg-surface-600 disabled:opacity-50 disabled:cursor-not-allowed"
							onclick={() => executeAction(action.action_id)}
							disabled={!!executingActionId || isTerminal || card.status === 'executing'}
						>
							{#if executingActionId === action.action_id}
								Executing...
							{:else}
								{action.label}
								<span class="ml-1 text-surface-500">({action.target_platform})</span>
							{/if}
						</button>
					{/each}
				</div>
				{#if executeError}
					<p class="mt-2 text-xs text-red-400">{executeError}</p>
				{/if}
			</div>
		{/if}

		<!-- Metadata footer -->
		<div class="mt-4 border-t border-surface-700 pt-3">
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-surface-500">
				{#if card.confidence}
					<span>Confidence: {Math.round(card.confidence * 100)}%</span>
				{/if}
				<span>Category: {card.category}</span>
				<span class={statusColors[card.status] ?? 'text-surface-400'}>Status: {card.status}</span>
				{#if card.has_workspace}
					<a href="/workspace/{card.card_id}" class="text-violet-400 hover:text-violet-300 underline">Open Workspace</a>
				{/if}
				{#if card.created_at}
					<span>Created: {new Date(card.created_at).toLocaleString()}</span>
				{/if}
			</div>
		</div>
	</div>

	<!-- Action buttons -->
	<div class="border-t border-surface-700 px-5 py-3">
		{#if isActionable}
			{#if showDismissInput}
				<div class="flex gap-2">
					<input
						bind:value={dismissReason}
						placeholder="Reason (optional)"
						class="flex-1 rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500"
					/>
					<button
						class="rounded-lg bg-surface-600 px-4 py-2 text-sm font-medium text-surface-200 hover:bg-surface-500"
						onclick={dismiss}
						disabled={dismissing}
					>
						{dismissing ? 'Dismissing...' : 'Confirm'}
					</button>
					<button
						class="text-sm text-surface-400 hover:text-surface-200"
						onclick={() => (showDismissInput = false)}
					>
						Cancel
					</button>
				</div>
			{:else}
				<div class="flex gap-2">
					<button
						class="flex-1 rounded-lg bg-green-700/40 py-2 text-sm font-medium text-green-300 transition-colors hover:bg-green-700/60"
						onclick={approve}
						disabled={approving}
					>
						{approving ? 'Approving...' : 'Approve'}
					</button>
					<button
						class="flex-1 rounded-lg bg-surface-700/50 py-2 text-sm font-medium text-surface-400 transition-colors hover:bg-surface-700"
						onclick={() => (showDismissInput = true)}
					>
						Dismiss
					</button>
					<button
						class="rounded-lg bg-surface-700/30 px-3 py-2 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-700"
						onclick={archive}
						disabled={archiving}
						title="Archive this card"
					>
						{archiving ? '…' : 'Archive'}
					</button>
				</div>
			{/if}
		{:else if card.status === 'dismissed' || card.status === 'archived'}
			<div class="flex gap-2">
				<button
					class="flex-1 rounded-lg bg-laya-orange/15 py-2 text-sm font-medium text-laya-orange transition-colors hover:bg-laya-orange/25 disabled:opacity-50"
					onclick={reopen}
					disabled={reopening}
				>
					{reopening ? 'Reopening...' : card.status === 'archived' ? 'Unarchive' : 'Reopen'}
				</button>
				{#if card.status === 'dismissed'}
					<button
						class="rounded-lg bg-surface-700/30 px-3 py-2 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
						onclick={archive}
						disabled={archiving}
					>
						{archiving ? '…' : 'Archive'}
					</button>
				{/if}
			</div>
		{:else if card.status === 'completed' || card.status === 'failed'}
			<div class="flex gap-2">
				{#if card.status === 'failed'}
					<button
						class="flex-1 rounded-lg bg-orange-700/30 py-2 text-sm font-medium text-orange-300 transition-colors hover:bg-orange-700/50 disabled:opacity-50"
						onclick={approve}
						disabled={approving}
					>
						{approving ? 'Retrying...' : 'Retry'}
					</button>
				{/if}
				<button
					class="rounded-lg bg-surface-700/30 px-3 py-2 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
					onclick={archive}
					disabled={archiving}
				>
					{archiving ? '…' : 'Archive'}
				</button>
			</div>
		{/if}
	</div>
</div>
