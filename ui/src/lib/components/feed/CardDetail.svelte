<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';

	let {
		card,
		onclose
	}: { card: ActionCard; onclose: () => void } = $props();

	let approving = $state(false);
	let dismissing = $state(false);
	let dismissReason = $state('');
	let showDismissInput = $state(false);

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
		<button class="text-surface-400 hover:text-surface-100" onclick={onclose}>
			&#x2715;
		</button>
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
							class="rounded-lg border border-surface-600 bg-surface-700/50 px-3 py-1.5 text-xs font-medium text-surface-200 transition-colors hover:bg-surface-600"
						>
							{action.label}
							<span class="ml-1 text-surface-500">({action.target_platform})</span>
						</button>
					{/each}
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
				<span>Status: {card.status}</span>
				{#if card.has_workspace}
					<span class="text-violet-400">Has workspace</span>
				{/if}
				{#if card.created_at}
					<span>Created: {new Date(card.created_at).toLocaleString()}</span>
				{/if}
			</div>
		</div>
	</div>

	<!-- Action buttons for pending cards -->
	{#if card.status === 'pending'}
		<div class="border-t border-surface-700 px-5 py-3">
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
				</div>
			{/if}
		</div>
	{/if}
</div>
