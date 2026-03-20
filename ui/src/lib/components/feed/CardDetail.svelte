<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { chatOpen, chatInputPreset } from '$lib/stores/chat';
	import { marked } from 'marked';

	let {
		card,
		onclose,
		ongotocard
	}: { card: ActionCard; onclose: () => void; ongotocard?: (card: ActionCard) => void } = $props();

	let markingDone = $state(false);
	let approvingAgent = $state(false);
	let dismissing = $state(false);
	let archiving = $state(false);
	let reopening = $state(false);
	let copied = $state(false);
	let dismissReason = $state('');
	let showDismissInput = $state(false);
	let executingActionId = $state<string | null>(null);
	let executeError = $state<string | null>(null);
	let showDeleteConfirm = $state(false);
	let deleting = $state(false);

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-600 text-red-50',
		HIGH: 'bg-orange-500 text-orange-50',
		MEDIUM: 'bg-laya-coral/20 text-laya-coral',
		LOW: 'bg-laya-gold/25 text-laya-amber'
	};

	const priorityLabel: Record<string, string> = {
		CRITICAL: 'CRIT',
		HIGH: 'HIGH',
		MEDIUM: 'MED',
		LOW: 'LOW'
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
		summary: 'Summary',
		agent_result: 'Agent Result',
		agent_plan: 'Implementation Plan'
	};

	const terminalStatuses = new Set(['done', 'failed', 'dismissed', 'archived']);
	const actionableStatuses = new Set(['ready', 'requires_approval', 'agent_running', 'awaiting_input']);
	let isActionable = $derived(actionableStatuses.has(card.status));
	let isTerminal = $derived(terminalStatuses.has(card.status));

	const statusColors: Record<string, string> = {
		pending: 'text-yellow-400',
		ready: 'text-amber-400',
		requires_approval: 'text-violet-400',
		agent_running: 'text-violet-400',
		awaiting_input: 'text-yellow-400',
		done: 'text-green-500',
		failed: 'text-red-500',
		dismissed: 'text-surface-500',
		archived: 'text-surface-600'
	};

	const statusLabels: Record<string, string> = {
		pending: 'Processing',
		ready: 'Ready',
		requires_approval: 'Needs Approval',
		agent_running: 'Agent Running',
		awaiting_input: 'Input Needed',
		done: 'Done',
		failed: 'Failed',
		dismissed: 'Dismissed',
		archived: 'Archived'
	};

	async function executeAction(actionId: string) {
		executingActionId = actionId;
		card.selected_action_id = actionId;
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

	async function markDone() {
		markingDone = true;
		try {
			await engineApi.markCardDone(card.card_id);
			card.status = 'done';
		} finally {
			markingDone = false;
		}
	}

	async function approveAgent() {
		approvingAgent = true;
		try {
			await engineApi.approveAgent(card.card_id);
			card.status = 'agent_running';
		} finally {
			approvingAgent = false;
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

	function deleteCard() {
		// Optimistic: close immediately, fire API in background
		showDeleteConfirm = false;
		onclose();
		engineApi.deleteCard(card.card_id).catch(() => {
			// If delete fails, next WS reload will restore the card
		});
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
				{priorityLabel[card.priority] ?? card.priority}
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
			<!-- Go to card -->
			{#if ongotocard}
				<div class="group/act relative">
					<button
						onclick={() => ongotocard?.(card)}
						class="rounded p-1.5 text-surface-500 transition-colors hover:text-laya-orange"
						aria-label="Go to card"
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5" />
						</svg>
					</button>
					<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Go to card</span>
				</div>
			{/if}
			<!-- Copy card ID -->
			<div class="group/act relative">
				<button
					onclick={copyId}
					aria-label="Copy card ID"
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
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">{copied ? 'Copied!' : 'Copy card ID'}</span>
			</div>
			<!-- Chat about this card -->
			<div class="group/act relative">
				<button
					onclick={chatAbout}
					aria-label="Chat about this card"
					class="rounded p-1.5 text-surface-500 transition-colors hover:text-laya-orange"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/act:opacity-100">Chat about card</span>
			</div>
			<button aria-label="Close" class="rounded p-1.5 text-surface-400 transition-colors hover:text-surface-100" onclick={onclose}>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Scrollable content -->
	<div class="flex-1 overflow-y-auto px-5 py-4">
		<!-- Actor info -->
		{#if card.actor_name || card.actor_email}
			<div class="mb-3 flex flex-col gap-0.5">
				{#if card.actor_name}
					<div class="flex items-center gap-1.5">
						<span class="text-[10px] font-semibold uppercase tracking-wider text-surface-500">Actor</span>
						<span class="text-xs text-surface-300">{card.actor_name}</span>
					</div>
				{/if}
				{#if card.actor_email}
					<div class="flex items-center gap-1.5">
						<span class="text-[10px] font-semibold uppercase tracking-wider text-surface-500">Email</span>
						<span class="text-xs text-surface-400">{card.actor_email}</span>
					</div>
				{/if}
			</div>
		{/if}

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
				{:else if card.staged_output.type === 'agent_plan'}
					<div class="prose-plan max-h-96 overflow-y-auto rounded-lg border border-surface-700 bg-surface-900/50 p-4 text-sm text-surface-200">
						{@html marked(card.staged_output.content)}
					</div>
				{:else}
					<div class="prose-plan max-h-96 overflow-y-auto overflow-x-auto rounded-lg border border-surface-700 bg-surface-900/50 p-4 text-sm text-surface-200">
						{@html marked(card.staged_output.content)}
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
						{@const isSelected = card.selected_action_id === action.action_id}
						<button
							class="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed
								{isSelected
									? 'border-laya-orange/50 bg-laya-orange/15 text-laya-orange'
									: card.selected_action_id && !isSelected
										? 'border-surface-700 bg-surface-800/50 text-surface-500'
										: 'border-surface-600 bg-surface-700/50 text-surface-200 hover:bg-surface-600'}
								{!isSelected && card.selected_action_id ? 'opacity-50' : ''}"
							onclick={() => executeAction(action.action_id)}
							disabled={!!executingActionId || isTerminal}
						>
							{#if executingActionId === action.action_id}
								Executing...
							{:else}
								{#if isSelected}
									<span class="mr-1">&#10003;</span>
								{/if}
								{action.label}
								<span class="ml-1 {isSelected ? 'text-laya-orange/60' : 'text-surface-500'}">({action.target_platform})</span>
							{/if}
						</button>
					{/each}
				</div>
				{#if executeError}
					<p class="mt-2 text-xs text-red-400">{executeError}</p>
				{/if}
			</div>
		{/if}

		<!-- Source reference -->
		{#if card.source_ref}
			<div class="mt-4 border-t border-surface-700 pt-3">
				<h3 class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-surface-400">Source</h3>
				<div class="flex items-center gap-2">
					{#if card.source_url}
						<a
							href={card.source_url}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1 text-sm font-medium text-laya-orange hover:text-laya-peach transition-colors"
						>
							{card.source_ref}
							<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
							</svg>
						</a>
					{:else}
						<span class="text-sm font-medium text-surface-200">{card.source_ref}</span>
					{/if}
				</div>
			</div>
		{/if}

		<!-- Metadata footer -->
		<div class="mt-4 border-t border-surface-700 pt-3">
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-surface-500">
				{#if card.confidence}
					<span>Confidence: {Math.round(card.confidence * 100)}%</span>
				{/if}
				<span>Category: {card.category}</span>
				<span class={statusColors[card.status] ?? 'text-surface-400'}>Status: {statusLabels[card.status] ?? card.status}</span>
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
		{#if card.status === 'ready'}
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
						onclick={markDone}
						disabled={markingDone}
					>
						{markingDone ? 'Marking...' : 'Mark as Done'}
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
						{archiving ? '...' : 'Archive'}
					</button>
				</div>
			{/if}
		{:else if card.status === 'requires_approval'}
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
						onclick={markDone}
						disabled={markingDone}
					>
						{markingDone ? 'Marking...' : 'Mark as Done'}
					</button>
					<button
						class="flex-1 rounded-lg bg-violet-700/40 py-2 text-sm font-medium text-violet-300 transition-colors hover:bg-violet-700/60"
						onclick={approveAgent}
						disabled={approvingAgent}
					>
						{approvingAgent ? 'Starting...' : 'Approve Agent'}
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
						{archiving ? '...' : 'Archive'}
					</button>
				</div>
			{/if}
		{:else if card.status === 'dismissed' || card.status === 'archived' || card.status === 'done' || card.status === 'failed'}
			<div class="flex gap-2">
				<button
					class="flex-1 rounded-lg bg-laya-orange/15 py-2 text-sm font-medium text-laya-orange transition-colors hover:bg-laya-orange/25 disabled:opacity-50"
					onclick={reopen}
					disabled={reopening}
				>
					{reopening ? 'Reopening...' : card.status === 'archived' ? 'Unarchive' : card.status === 'failed' ? 'Retry' : 'Reopen'}
				</button>
				{#if card.status !== 'archived'}
					<button
						class="rounded-lg bg-surface-700/30 px-3 py-2 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
						onclick={archive}
						disabled={archiving}
					>
						{archiving ? '...' : 'Archive'}
					</button>
				{/if}
				{#if card.status === 'archived'}
					<button
						class="rounded-lg bg-red-950/60 px-3 py-2 text-sm text-red-400 transition-colors hover:bg-red-900/60 disabled:opacity-50"
						onclick={() => (showDeleteConfirm = true)}
						title="Delete permanently"
						disabled={deleting}
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
						</svg>
					</button>
				{/if}
			</div>
		{:else}
			<div class="flex gap-2">
				<button
					class="rounded-lg bg-surface-700/30 px-3 py-2 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
					onclick={archive}
					disabled={archiving}
				>
					{archiving ? '...' : 'Archive'}
				</button>
			</div>
		{/if}
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
					onclick={() => (showDeleteConfirm = false)}
					disabled={deleting}
				>
					Cancel
				</button>
				<button
					class="rounded-md bg-red-700 px-3 py-1.5 text-xs font-medium text-red-50 transition-colors hover:bg-red-600 disabled:opacity-50"
					onclick={deleteCard}
					disabled={deleting}
				>
					{deleting ? 'Deleting...' : 'Delete permanently'}
				</button>
			</div>
		</div>
	</div>
{/if}
