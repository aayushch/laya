<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import { chatOpen, chatInputPreset } from '$lib/stores/chat';
	import { lastMessage } from '$lib/stores/websocket';
	import { marked } from 'marked';
	import ClassificationDialog from './ClassificationDialog.svelte';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';

	let {
		card,
		onclose,
		ondismiss,
		ongotocard,
		onlink
	}: { card: ActionCard; onclose: () => void; ondismiss?: () => void; ongotocard?: (card: ActionCard) => void; onlink?: (card: ActionCard) => void } = $props();

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
	let editingActionId = $state<string | null>(null);
	let editedPayload = $state<Record<string, string>>({});
	let savingPayload = $state(false);
	// Per-action spinner state for AI Polish. Seeded from `_polishing` flags
	// persisted in the action payload, so re-mounting the panel mid-flight
	// still shows the spinner. WS `action_payload_updated` events keep it
	// in sync across navigations and clients.
	let polishingActionIds = $state(new Set<string>());
	let polishErrors = $state<Record<string, string>>({});
	let showDeleteConfirm = $state(false);
	let deleting = $state(false);
	let showClassificationDialog = $state(false);
	let bookmarking = $state(false);
	let unlinkingCard = $state(false);
	let showResearchInput = $state(false);
	let researchPrompt = $state('');
	let startingResearch = $state(false);
	let actorTruncated = $state(false);
	let emailTruncated = $state(false);

	// Watches an element for text overflow and reports the result via callback.
	// The text param is included so the action's `update` re-runs (and re-measures)
	// when the underlying text changes — ResizeObserver alone fires only on size changes,
	// so it would miss a shorter string fitting after a card switch.
	function trackTruncation(node: HTMLElement, params: { onChange: (t: boolean) => void; text: string }) {
		let { onChange } = params;
		const measure = () => onChange(node.scrollWidth > node.clientWidth + 1);
		measure();
		const ro = new ResizeObserver(measure);
		ro.observe(node);
		return {
			update(next: { onChange: (t: boolean) => void; text: string }) {
				onChange = next.onChange;
				queueMicrotask(measure);
			},
			destroy() { ro.disconnect(); }
		};
	}

	async function unlinkCard() {
		if (!card.context_id) return;
		unlinkingCard = true;
		try {
			await engineApi.unlinkContextGroup(card.context_id);
		} finally {
			unlinkingCard = false;
		}
	}

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
		OPS: 'border-amber-500 text-amber-400',
		SALES: 'border-sky-500 text-sky-400',
		HR: 'border-rose-500 text-rose-400',
		FINANCE: 'border-teal-500 text-teal-400'
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
			const mods = editingActionId === actionId && Object.keys(editedPayload).length > 0
				? editedPayload
				: undefined;
			const result = await engineApi.executeAction(card.card_id, actionId, mods);
			card.status = result.status as ActionCard['status'];
			if (result.error) {
				card.last_error = result.error;
			} else {
				card.last_error = undefined;
			}
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Execution failed';
		} finally {
			executingActionId = null;
		}
	}

	/** Identify the main editable text field in an action payload (body, comment, message, etc.). */
	function getEditableTextField(payload: Record<string, unknown>): string | null {
		for (const key of ['body', 'comment', 'message', 'description']) {
			if (typeof payload[key] === 'string' && (payload[key] as string).length > 0) return key;
		}
		return null;
	}

	function startEditing(action: { action_id: string; payload: Record<string, unknown> }) {
		editingActionId = action.action_id;
		const p = action.payload;
		// Extract all string fields for editing — works for email (to/subject/body),
		// Bitbucket/GitHub (comment), Slack (message), Jira (comment), etc.
		// Skip underscore-prefixed keys (e.g. _edited, _polishing, _polish_error)
		// which are internal UI-state flags, not user-editable content.
		editedPayload = {};
		for (const [key, value] of Object.entries(p)) {
			if (key.startsWith('_')) continue;
			if (typeof value === 'string' && value.length > 0) {
				editedPayload[key] = value;
			}
		}
	}

	async function polishDraft(action: { action_id: string }) {
		if (polishingActionIds.has(action.action_id)) return;
		// Optimistic spinner — confirmed by the WS echo once the server flips
		// `_polishing` to true, then cleared when polish completes.
		polishingActionIds = new Set([...polishingActionIds, action.action_id]);
		const { [action.action_id]: _drop, ...restErrors } = polishErrors;
		polishErrors = restErrors;
		try {
			await engineApi.polishActionPayload(card.card_id, action.action_id);
		} catch (err) {
			const next = new Set(polishingActionIds);
			next.delete(action.action_id);
			polishingActionIds = next;
			polishErrors = {
				...polishErrors,
				[action.action_id]: err instanceof Error ? err.message : 'Polish failed'
			};
		}
	}

	// Seed spinner state from persisted `_polishing` flags whenever the viewed
	// card changes (fresh panel mount, or user switches cards). The WS effect
	// below handles subsequent updates — so we don't re-seed on every mutation,
	// which would otherwise wipe client-side errors from failed API calls.
	let _seededCardId = $state<string | null>(null);
	$effect(() => {
		if (_seededCardId === card.card_id) return;
		_seededCardId = card.card_id;
		const actions = card.suggested_actions ?? [];
		const seed = new Set<string>();
		const errs: Record<string, string> = {};
		for (const a of actions) {
			const p = a.payload as Record<string, unknown> | undefined;
			if (p?._polishing === true) seed.add(a.action_id);
			if (typeof p?._polish_error === 'string') errs[a.action_id] = p._polish_error as string;
		}
		polishingActionIds = seed;
		polishErrors = errs;
	});

	// Listen for per-action payload updates (polish start/complete, manual save).
	// The feed's WS handler is responsible for mutating groups[] — this handler
	// ONLY updates local spinner/error state.
	//
	// The body runs inside untrack() so the effect depends only on $lastMessage.
	// Without untrack, reading card.suggested_actions / action.payload here
	// tracks those reactive proxies; the subsequent writes to action.payload
	// then re-trigger this same effect, loop without bound, starve the
	// microtask queue, and freeze the UI (blocking savePayload's finally, so
	// "Saving..." never clears, and blocking any navigation after Polish).
	$effect(() => {
		const msg = $lastMessage;
		if (!msg || msg.type !== 'action_payload_updated') return;
		untrack(() => {
			if (msg.card_id !== card.card_id) return;
			const actionId = (msg as { action_id?: string }).action_id;
			const newPayload = (msg.payload as { payload?: Record<string, unknown> })?.payload;
			if (!actionId || !newPayload) return;
			if (newPayload._polishing === true) {
				polishingActionIds = new Set([...polishingActionIds, actionId]);
			} else if (newPayload._polishing === false) {
				const next = new Set(polishingActionIds);
				next.delete(actionId);
				polishingActionIds = next;
			}
			const err = newPayload._polish_error;
			if (typeof err === 'string' && err) {
				polishErrors = { ...polishErrors, [actionId]: err };
			} else if (newPayload._polishing === false && actionId in polishErrors) {
				const { [actionId]: _drop, ...rest } = polishErrors;
				polishErrors = rest;
			}
		});
	});

	async function savePayload(action: { action_id: string; payload: Record<string, unknown> }) {
		savingPayload = true;
		try {
			await engineApi.updateActionPayload(card.card_id, action.action_id, editedPayload);
			// Update the local action payload so the read-only view reflects saved
			// changes. Also flip _edited locally so the Polish button appears
			// immediately — the WS echo will confirm this shortly.
			Object.assign(action.payload, editedPayload);
			action.payload._edited = true;
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Failed to save draft';
		} finally {
			savingPayload = false;
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
			const result = await engineApi.reopenCard(card.card_id);
			card.status = result.status as ActionCard['status'];
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

	async function toggleBookmark() {
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

	// Research is available on cards without an active workspace, in eligible statuses
	const researchEligible = $derived(
		!card.has_workspace &&
		['ready', 'done', 'dismissed', 'failed'].includes(card.status)
	);

	async function startResearch() {
		startingResearch = true;
		try {
			await engineApi.startResearch(card.card_id, {
				prompt: researchPrompt || undefined
			});
			card.status = 'agent_running';
			card.has_workspace = true;
			showResearchInput = false;
			researchPrompt = '';
		} finally {
			startingResearch = false;
		}
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
					<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">Go to card</span>
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
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">{copied ? 'Copied!' : 'Copy card ID'}</span>
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
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">Chat about card</span>
			</div>
			<!-- Bookmark -->
			<div class="group/act relative">
				<button
					onclick={toggleBookmark}
					aria-label={card.bookmarked_at ? 'Remove bookmark' : 'Bookmark card'}
					class="rounded p-1.5 transition-colors {card.bookmarked_at ? 'text-laya-orange' : 'text-surface-500 hover:text-laya-orange'}"
					disabled={bookmarking}
				>
					<svg class="h-4 w-4" fill={card.bookmarked_at ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/act:opacity-100">{card.bookmarked_at ? 'Remove Bookmark' : 'Bookmark'}</span>
			</div>
			<button aria-label="Dismiss card" class="rounded p-1.5 text-surface-400 transition-colors hover:text-surface-100" onclick={() => ondismiss ? ondismiss() : onclose()}>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Scrollable content -->
	<div class="flex-1 overflow-y-auto px-5 py-4">
		<!-- Source platform + subject ID + actor info -->
		{#if card.entity_id || card.actor_name || card.actor_email}
			<div class="mb-3 flex flex-col gap-0.5">
				{#if card.entity_id}
					<div class="mb-1 flex items-center gap-1.5 min-w-0">
						<PlatformBadge platform={card.entity_id.split(':')[0]} />
						{#if card.source_ref}
							{#if card.source_url}
								<a
									href={card.source_url}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1 text-xs font-medium text-laya-orange hover:text-laya-peach transition-colors min-w-0 truncate"
								>
									<span class="truncate">{card.source_ref}</span>
									<svg class="h-2.5 w-2.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
									</svg>
								</a>
							{:else}
								<span class="text-xs font-medium text-surface-400 truncate">{card.source_ref}</span>
							{/if}
						{/if}
					</div>
				{/if}
				{#if card.actor_name}
					<div class="flex items-center gap-1.5 min-w-0">
						<span class="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Actor</span>
						<span class="group/actor relative min-w-0 flex-1">
							<span use:trackTruncation={{ onChange: (t) => (actorTruncated = t), text: card.actor_name }} class="block truncate text-xs text-surface-300">{card.actor_name}</span>
							{#if actorTruncated}
								<span class="pointer-events-none absolute left-0 top-full z-50 mt-1 max-w-xs break-all whitespace-normal rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/actor:opacity-100">
									{card.actor_name}
								</span>
							{/if}
						</span>
					</div>
				{/if}
				{#if card.actor_email}
					<div class="flex items-center gap-1.5 min-w-0">
						<span class="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Email</span>
						<span class="group/email relative min-w-0 flex-1">
							<span use:trackTruncation={{ onChange: (t) => (emailTruncated = t), text: card.actor_email }} class="block truncate text-xs text-surface-400">{card.actor_email}</span>
							{#if emailTruncated}
								<span class="pointer-events-none absolute left-0 top-full z-50 mt-1 max-w-xs break-all whitespace-normal rounded-md border border-transparent glass-card bg-surface-800/40 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 transition-opacity duration-75 group-hover/email:opacity-100">
									{card.actor_email}
								</span>
							{/if}
						</span>
					</div>
				{/if}
			</div>
		{/if}

		<!-- Header + summary -->
		<h2 class="mb-2 text-lg font-semibold text-surface-50">{card.header}</h2>
		<p class="mb-5 text-laya-base text-surface-300">{card.summary}</p>

		<!-- Intelligence report -->
		{#if card.intelligence && card.intelligence.length > 0}
			<div class="mb-5">
				<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Intelligence Report</h3>
				<ul class="space-y-1.5">
					{#each card.intelligence as point}
						<li class="flex items-start gap-2 text-laya-base text-surface-300">
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
					<div class="prose-plan max-h-96 overflow-y-auto rounded-lg border border-surface-700 bg-surface-900/50 p-4 text-laya-base text-surface-200">
						{@html marked(card.staged_output.content)}
					</div>
				{:else}
					<div class="prose-plan max-h-96 overflow-y-auto overflow-x-auto rounded-lg border border-surface-700 bg-surface-900/50 p-4 text-laya-base text-surface-200">
						{@html marked(card.staged_output.content)}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Suggested actions -->
		{#if card.suggested_actions && card.suggested_actions.length > 0}
			<div class="mb-5">
				<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">Suggested Actions</h3>
				{#each card.suggested_actions as action}
					{@const isSelected = card.selected_action_id === action.action_id}
					{@const payload = action.payload}
					{@const editableField = payload ? getEditableTextField(payload) : null}

					<!-- Action payload preview — works for any action with editable text (email body, PR comment, Slack message, etc.) -->
					{#if editableField}
						{@const isEditing = editingActionId === action.action_id}
						{@const isPolishing = polishingActionIds.has(action.action_id)}
						{@const hasEdits = payload._edited === true}
						{@const polishErrorMsg = polishErrors[action.action_id]}
						<div class="relative mb-2 rounded-lg border border-surface-700 bg-surface-900/50 p-3">
							{#if !isEditing}
								<!-- Read-only view: show metadata fields, then the main text -->
								{#each Object.entries(payload) as [key, value]}
									{#if !key.startsWith('_') && typeof value === 'string' && value.length > 0 && key !== editableField}
										<div class="mb-1.5 flex items-center gap-1.5 text-[11px]">
											<span class="font-medium text-surface-500 capitalize">{key}:</span>
											<span class="text-surface-300">{value}</span>
										</div>
									{/if}
								{/each}
								<div class="max-h-48 overflow-y-auto whitespace-pre-wrap text-laya-base text-surface-200">{payload[editableField]}</div>
							{:else}
								<!-- Edit mode: inputs for metadata, textarea for main text -->
								{#each Object.entries(editedPayload) as [key]}
									{#if key !== editableField}
										<div class="mb-1.5 flex items-center gap-1.5 text-[11px]">
											<span class="shrink-0 font-medium text-surface-500 capitalize">{key}:</span>
											<input
												type="text"
												class="w-full rounded border border-surface-600 bg-surface-800 px-1.5 py-0.5 text-[11px] text-surface-200 outline-none focus:border-laya-orange/50"
												bind:value={editedPayload[key]}
											/>
										</div>
									{/if}
								{/each}
								<textarea
									class="w-full resize-y rounded border border-surface-600 bg-surface-800 p-2 text-sm text-surface-200 outline-none focus:border-laya-orange/50"
									rows="6"
									bind:value={editedPayload[editableField]}
								></textarea>
							{/if}
							<!-- Polish-in-flight overlay. Covers payload preview; blocks interaction
							     while the LLM is rewriting. Survives navigation because _polishing
							     is persisted in the action payload. -->
							{#if isPolishing}
								<div class="pointer-events-auto absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg bg-surface-900/70 backdrop-blur-sm">
									<svg class="h-6 w-6 animate-spin text-laya-orange" fill="none" viewBox="0 0 24 24">
										<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
										<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
									</svg>
									<span class="text-[11px] font-medium text-laya-orange">Polishing draft…</span>
								</div>
							{/if}
							<!-- Edit / Save / Cancel / Polish controls -->
							{#if !isTerminal}
								<div class="mt-2 flex items-center justify-end gap-3">
									{#if polishErrorMsg && !isPolishing}
										<span class="mr-auto text-[11px] text-red-400">{polishErrorMsg}</span>
									{/if}
									{#if !isEditing}
										<button
											class="text-[11px] text-surface-400 hover:text-laya-orange transition-colors disabled:opacity-40 disabled:hover:text-surface-400"
											onclick={() => startEditing(action)}
											disabled={isPolishing}
										>
											Edit draft
										</button>
										{#if hasEdits}
											<button
												class="inline-flex items-center gap-1 text-[11px] font-medium text-laya-gold hover:text-laya-peach transition-colors disabled:opacity-40 disabled:hover:text-laya-gold"
												onclick={() => polishDraft(action)}
												disabled={isPolishing}
												title="Rewrite this draft with AI to polish the phrasing"
											>
												<svg class="h-3 w-3" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
													<path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9L12 2zm7 11l.95 2.8L22.75 16.75l-2.8.95L19 20.5l-.95-2.8L15.25 16.75l2.8-.95L19 13zM5 14l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7L5 14z" />
												</svg>
												Polish
											</button>
										{/if}
									{:else}
										<button
											class="text-[11px] text-surface-400 hover:text-surface-200 transition-colors"
											onclick={() => { editingActionId = null; editedPayload = {}; }}
											disabled={savingPayload}
										>
											Cancel
										</button>
										<button
											class="text-[11px] font-medium text-laya-orange hover:text-laya-gold transition-colors disabled:opacity-50"
											onclick={() => savePayload(action)}
											disabled={savingPayload}
										>
											{savingPayload ? 'Saving...' : 'Save'}
										</button>
									{/if}
								</div>
							{/if}
						</div>
					{/if}

					<div class="mb-2 flex flex-wrap gap-2">
						<button
							class="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed
								{isSelected
									? isTerminal
										? 'border-laya-orange/50 bg-laya-orange/15 text-laya-orange'
										: 'border-surface-500 border-dashed bg-surface-800/50 text-surface-400'
									: card.selected_action_id && !isSelected
										? isTerminal
											? 'border-surface-700 bg-surface-800/50 text-surface-500 opacity-50'
											: 'border-surface-600 bg-surface-700/50 text-surface-200 hover:bg-surface-600'
										: 'border-surface-600 bg-surface-700/50 text-surface-200 hover:bg-surface-600'}"
							onclick={() => executeAction(action.action_id)}
							disabled={!!executingActionId || isTerminal}
						>
							{#if executingActionId === action.action_id}
								Executing...
							{:else}
								{#if isSelected}
									<span class="mr-1">{isTerminal ? '✓' : '↩'}</span>
								{/if}
								{action.label}
								<span class="ml-1 {isSelected && isTerminal ? 'text-laya-orange/60' : 'text-surface-500'}">({action.target_platform})</span>
							{/if}
						</button>
					</div>
				{/each}
				{#if executeError}
					<p class="mt-2 text-xs text-red-400">{executeError}</p>
				{/if}
			</div>
		{/if}

		<!-- Metadata -->
		<div class="mt-4 border-t border-surface-700 pt-3">
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-surface-500">
				{#if card.confidence}
					<span>Confidence: {Math.round(card.confidence * 100)}%</span>
				{/if}
				<span>Category: {card.category}</span>
				{#if card.status === 'failed' && card.last_error}
					<span class="{statusColors[card.status]} relative group cursor-help">
						Status: {statusLabels[card.status]}
						<span class="invisible group-hover:visible absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2.5 py-1.5 text-[11px] leading-tight bg-surface-800 border border-surface-600 text-surface-300 rounded shadow-lg whitespace-normal max-w-[280px] w-max z-50">
							{card.last_error}
						</span>
					</span>
				{:else}
					<span class={statusColors[card.status] ?? 'text-surface-400'}>Status: {statusLabels[card.status] ?? card.status}</span>
				{/if}
				{#if card.created_at}
					<span>Created: {new Date(card.created_at).toLocaleString()}</span>
				{/if}
			</div>
		</div>
	</div>

	<!-- Footer -->
	<div class="border-t border-surface-700 px-5 py-2">
		<!-- Secondary actions -->
		<div class="flex items-center justify-end gap-1">
			{#if card.has_workspace}
				<a
					href="/workspace/{card.card_id}"
					class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-violet-400/80 transition-colors hover:bg-violet-500/15 hover:text-violet-300"
					onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/workspace/${card.card_id}`); }}
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
					Workspace
				</a>
			{/if}
			<button
				class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-surface-200"
				onclick={() => (showClassificationDialog = true)}
			>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
				Classify
			</button>
			{#if onlink}
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-surface-200"
					onclick={() => onlink(card)}
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
					Link
				</button>
			{/if}
			{#if card.context_id}
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-red-400 disabled:opacity-50"
					onclick={unlinkCard}
					disabled={unlinkingCard}
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /><line x1="4" y1="4" x2="20" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
					{unlinkingCard ? '...' : 'Unlink'}
				</button>
			{/if}
			{#if researchEligible}
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-cyan-400"
					onclick={() => (showResearchInput = true)}
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
					Research
				</button>
			{/if}
			{#if card.status === 'archived'}
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-red-400 disabled:opacity-50"
					onclick={() => (showDeleteConfirm = true)}
					disabled={deleting}
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
					Delete
				</button>
			{/if}
		</div>

		<!-- Primary actions -->
		<div class="mt-3">
			{#if showResearchInput}
				<div class="flex flex-col gap-2">
					<div class="flex items-center gap-2 text-xs text-surface-400">
						<svg class="h-3.5 w-3.5 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
						Start Research
					</div>
					<input
						bind:value={researchPrompt}
						placeholder="What would you like to research? (optional)"
						class="flex-1 rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-surface-50 placeholder-surface-500"
					/>
					<div class="flex gap-2">
						<button
							class="flex-1 rounded-md bg-cyan-700/40 px-2 py-1.5 text-xs font-medium text-cyan-300 transition-colors hover:bg-cyan-700/60 disabled:opacity-50"
							onclick={startResearch}
							disabled={startingResearch}
						>
							{startingResearch ? 'Starting...' : 'Start'}
						</button>
						<button
							class="text-sm text-surface-400 hover:text-surface-200"
							onclick={() => { showResearchInput = false; researchPrompt = ''; }}
						>
							Cancel
						</button>
					</div>
				</div>
			{:else if showDismissInput}
				<div class="flex gap-2">
					<input
						bind:value={dismissReason}
						placeholder="Reason (optional)"
						class="flex-1 rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-surface-50 placeholder-surface-500"
					/>
					<button
						class="rounded-md bg-surface-600 px-3 py-1.5 text-xs font-medium text-surface-200 hover:bg-surface-500"
						onclick={dismiss}
						disabled={dismissing}
					>
						{dismissing ? '...' : 'Confirm'}
					</button>
					<button
						class="text-sm text-surface-400 hover:text-surface-200"
						onclick={() => (showDismissInput = false)}
					>
						Cancel
					</button>
				</div>
			{:else if card.status === 'ready'}
				<div class="flex gap-2">
					<button
						class="flex-1 rounded-md bg-green-700/40 px-2 py-1.5 text-xs font-medium text-green-300 transition-colors hover:bg-green-700/60 disabled:opacity-50"
						onclick={markDone}
						disabled={markingDone}
					>
						{markingDone ? '...' : 'Done'}
					</button>
					<button
						class="flex-1 rounded-md bg-surface-700/50 px-2 py-1.5 text-xs font-medium text-surface-400 transition-colors hover:bg-surface-700"
						onclick={() => (showDismissInput = true)}
					>
						Dismiss
					</button>
					<button
						class="flex-1 rounded-md bg-surface-700/30 px-2 py-1.5 text-xs font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
						onclick={archive}
						disabled={archiving}
					>
						{archiving ? '...' : 'Archive'}
					</button>
				</div>
			{:else if card.status === 'requires_approval'}
				<div class="flex gap-2">
					<button
						class="flex-1 rounded-md bg-violet-700/40 px-2 py-1.5 text-xs font-medium text-violet-300 transition-colors hover:bg-violet-700/60 disabled:opacity-50"
						onclick={approveAgent}
						disabled={approvingAgent}
					>
						{approvingAgent ? '...' : 'Approve'}
					</button>
					<button
						class="flex-1 rounded-md bg-green-700/40 px-2 py-1.5 text-xs font-medium text-green-300 transition-colors hover:bg-green-700/60 disabled:opacity-50"
						onclick={markDone}
						disabled={markingDone}
					>
						{markingDone ? '...' : 'Done'}
					</button>
					<button
						class="flex-1 rounded-md bg-surface-700/50 px-2 py-1.5 text-xs font-medium text-surface-400 transition-colors hover:bg-surface-700"
						onclick={() => (showDismissInput = true)}
					>
						Dismiss
					</button>
					<button
						class="flex-1 rounded-md bg-surface-700/30 px-2 py-1.5 text-xs font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
						onclick={archive}
						disabled={archiving}
					>
						{archiving ? '...' : 'Archive'}
					</button>
				</div>
			{:else if card.status === 'dismissed' || card.status === 'archived' || card.status === 'done' || card.status === 'failed'}
				<div class="flex gap-2">
					<button
						class="flex-1 rounded-md bg-laya-orange/15 px-2 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/25 disabled:opacity-50"
						onclick={reopen}
						disabled={reopening}
					>
						{reopening ? 'Reopening...' : card.status === 'archived' ? 'Unarchive' : card.status === 'failed' ? 'Retry' : 'Reopen'}
					</button>
					{#if card.status !== 'archived'}
						<button
							class="flex-1 rounded-md bg-surface-700/30 px-2 py-1.5 text-xs font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
							onclick={archive}
							disabled={archiving}
						>
							{archiving ? '...' : 'Archive'}
						</button>
					{/if}
				</div>
			{:else}
				<div class="flex gap-2">
					<button
						class="flex-1 rounded-md bg-surface-700/30 px-2 py-1.5 text-xs font-medium text-surface-500 transition-colors hover:bg-surface-700 disabled:opacity-50"
						onclick={archive}
						disabled={archiving}
					>
						{archiving ? '...' : 'Archive'}
					</button>
				</div>
			{/if}
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

{#if showClassificationDialog}
	<ClassificationDialog
		{card}
		onclose={() => (showClassificationDialog = false)}
	/>
{/if}
