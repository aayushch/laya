<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { ActionCard, ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from '$lib/components/chat/ChatMessage.svelte';
	import { marked } from 'marked';

	let cards = $state<ActionCard[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Action execution & draft editing state
	let executingActionId = $state<string | null>(null);
	let executeError = $state<string | null>(null);
	let editingActionId = $state<string | null>(null);
	let editedPayload = $state<Record<string, string>>({});
	let savingPayload = $state(false);

	const terminalStatuses = new Set(['done', 'failed', 'dismissed', 'archived']);

	/** Identify the main editable text field in an action payload. */
	function getEditableTextField(payload: Record<string, unknown>): string | null {
		for (const key of ['body', 'comment', 'message', 'description']) {
			if (typeof payload[key] === 'string' && (payload[key] as string).length > 0) return key;
		}
		return null;
	}

	function startEditing(action: { action_id: string; payload: Record<string, unknown> }) {
		editingActionId = action.action_id;
		const p = action.payload;
		editedPayload = {};
		for (const [key, value] of Object.entries(p)) {
			if (typeof value === 'string' && value.length > 0) {
				editedPayload[key] = value;
			}
		}
	}

	async function savePayload(card: ActionCard, action: { action_id: string; payload: Record<string, unknown> }) {
		savingPayload = true;
		try {
			await engineApi.updateActionPayload(card.card_id, action.action_id, editedPayload);
			Object.assign(action.payload, editedPayload);
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Failed to save draft';
		} finally {
			savingPayload = false;
		}
	}

	async function executeAction(card: ActionCard, actionId: string) {
		executingActionId = actionId;
		executeError = null;
		try {
			const mods = editingActionId === actionId && Object.keys(editedPayload).length > 0
				? editedPayload
				: undefined;
			const result = await engineApi.executeAction(card.card_id, actionId, mods);
			card.status = result.status as ActionCard['status'];
			card.selected_action_id = actionId;
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Execution failed';
		} finally {
			executingActionId = null;
		}
	}

	// Chat state
	let chatMessages = $state<ChatMessageType[]>([]);
	let chatInput = $state('');
	let chatSending = $state(false);
	let chatConversationId = $state<string | null>(null);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();

	// Extract card IDs from query params (e.g. ?cards=card_1&cards=card_2)
	const cardIds = $derived($page.url.searchParams.getAll('cards'));

	const outputTypeLabels: Record<string, string> = {
		draft_reply: 'Draft Reply',
		code_fix: 'Code Fix',
		briefing: 'Briefing',
		summary: 'Summary',
		agent_result: 'Agent Result',
		agent_plan: 'Agent Plan'
	};

	const priorityColors: Record<string, string> = {
		CRITICAL: 'bg-red-500/20 text-red-400',
		HIGH: 'bg-orange-500/20 text-orange-400',
		MEDIUM: 'bg-surface-700 text-surface-300',
		LOW: 'bg-laya-gold/25 text-laya-amber'
	};

	onMount(async () => {
		await loadCards();
	});

	async function loadCards() {
		loading = true;
		error = null;
		try {
			const results = await Promise.all(cardIds.map((id) => engineApi.getCard(id)));
			cards = results;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load cards';
		} finally {
			loading = false;
		}
	}

	/** Extract platform name from entity_id */
	function extractPlatform(c: ActionCard): string {
		if (c.entity_id) return c.entity_id.split(':')[0];
		return 'unknown';
	}

	/** Build combined card context for system prompt injection. */
	function buildCardContext(): string {
		const sections: string[] = [];
		sections.push(`The user is viewing ${cards.length} related card(s) from an Omni summary item.`);
		sections.push('');

		for (const c of cards) {
			const lines = [
				`--- Card: ${c.card_id} ---`,
				`Title: ${c.header}`,
				`Summary: ${c.summary}`,
				`Priority: ${c.priority} | Status: ${c.status} | Persona: ${c.persona} | Category: ${c.category}`,
				`Platform: ${extractPlatform(c)}`,
			];
			if (c.actor_name) lines.push(`Actor: ${c.actor_name}`);
			if (c.intelligence && c.intelligence.length > 0) {
				lines.push('Intelligence:');
				c.intelligence.forEach((p) => lines.push(`- ${p}`));
			}
			if (c.staged_output) {
				lines.push(`Staged Output (${c.staged_output.type}):`, c.staged_output.content);
			}
			if (c.source_ref) lines.push(`Source: ${c.source_ref}`);
			if (c.source_url) lines.push(`URL: ${c.source_url}`);
			lines.push('');
			sections.push(lines.join('\n'));
		}

		return sections.join('\n');
	}

	function scrollToBottom() {
		if (messagesEl) {
			setTimeout(() => {
				if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
			}, 0);
		}
	}

	function resizeTextarea() {
		if (!textareaEl) return;
		textareaEl.style.height = 'auto';
		textareaEl.style.height = Math.min(textareaEl.scrollHeight, 160) + 'px';
	}

	async function sendMessage() {
		if (!chatInput.trim() || chatSending || cards.length === 0) return;

		const message = chatInput.trim();
		chatInput = '';
		chatSending = true;
		if (textareaEl) textareaEl.style.height = 'auto';

		// Add user message to UI immediately
		const userMsg: ChatMessageType = {
			message_id: `tmp_${Date.now()}`,
			timestamp: new Date().toISOString(),
			role: 'user',
			content: message,
			referenced_cards: [],
			referenced_events: [],
			conversation_id: chatConversationId ?? undefined
		};
		chatMessages = [...chatMessages, userMsg];
		scrollToBottom();

		try {
			const response = await engineApi.sendChat(
				message,
				chatConversationId ?? undefined,
				buildCardContext()
			);

			if (response.message.conversation_id) {
				chatConversationId = response.message.conversation_id;
			}

			chatMessages = [...chatMessages, response.message];
			scrollToBottom();
		} catch (e) {
			const errMsg: ChatMessageType = {
				message_id: `err_${Date.now()}`,
				timestamp: new Date().toISOString(),
				role: 'assistant',
				content: `Sorry, I encountered an error: ${e instanceof Error ? e.message : 'Unknown error'}`,
				referenced_cards: [],
				referenced_events: [],
				conversation_id: chatConversationId ?? undefined
			};
			chatMessages = [...chatMessages, errMsg];
			scrollToBottom();
		} finally {
			chatSending = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			sendMessage();
		}
	}

	// Resizable panels
	let chatWidth = $state(400);
	let dragging = $state(false);
	const MIN_CHAT = 280;
	const MAX_CHAT = 700;

	function onDragStart(e: MouseEvent) {
		e.preventDefault();
		dragging = true;

		const onMove = (ev: MouseEvent) => {
			// chatWidth = distance from right edge of container
			const container = (e.target as HTMLElement).closest('.insight-container') as HTMLElement;
			if (!container) return;
			const rect = container.getBoundingClientRect();
			const newWidth = rect.right - ev.clientX;
			chatWidth = Math.max(MIN_CHAT, Math.min(MAX_CHAT, newWidth));
		};

		const onUp = () => {
			dragging = false;
			window.removeEventListener('mousemove', onMove);
			window.removeEventListener('mouseup', onUp);
		};

		window.addEventListener('mousemove', onMove);
		window.addEventListener('mouseup', onUp);
	}

	function goBack() {
		goto('/omni');
	}
</script>

<svelte:head>
	<title>{cards.length === 1 ? cards[0]?.header : `${cards.length} Cards`} - Omni - Laya</title>
</svelte:head>

{#if loading}
	<div class="flex h-full items-center justify-center">
		<p class="text-sm text-surface-400">Loading cards...</p>
	</div>
{:else if error}
	<div class="flex h-full flex-col items-center justify-center gap-3">
		<p class="text-sm text-red-400">{error}</p>
		<button
			class="rounded-lg bg-surface-700 px-4 py-2 text-sm text-surface-200 hover:bg-surface-600"
			onclick={loadCards}
		>Retry</button>
	</div>
{:else if cards.length > 0}
	<div class="insight-container flex h-[calc(100%+3rem)] -m-6" class:select-none={dragging}>
		<!-- Left: Card content(s) -->
		<div class="flex-1 overflow-y-auto p-6">
			<!-- Back button -->
			<div class="mb-5 flex items-center gap-3">
				<button
					onclick={goBack}
					class="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200"
				>
					<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path d="M15 19l-7-7 7-7" />
					</svg>
					Omni
				</button>
				{#if cards.length > 1}
					<div class="h-4 w-px bg-surface-700"></div>
					<span class="text-xs text-surface-400">{cards.length} related cards</span>
				{/if}
			</div>

			<!-- Render each card -->
			{#each cards as card, idx}
				<div class="mb-4 rounded-xl p-5 insight-card-bg">
					<!-- Card meta bar -->
					<div class="mb-3 flex items-center gap-2 flex-wrap">
						<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider {priorityColors[card.priority]}">{card.priority}</span>
						<span class="text-[10px] text-surface-500 uppercase tracking-wider">{card.persona}</span>
						<span class="text-[10px] text-surface-500 uppercase tracking-wider">{card.category}</span>
						{#if card.entity_id}
							<span class="text-[10px] text-surface-500 uppercase tracking-wider">{extractPlatform(card)}</span>
						{/if}
						<span class="rounded bg-surface-800 px-1.5 py-0.5 text-[10px] text-surface-400">{card.status}</span>
					</div>

					<!-- Actor -->
					{#if card.actor_name}
						<div class="mb-3 flex items-center gap-2">
							<div class="flex h-6 w-6 items-center justify-center rounded-full bg-surface-700 text-[10px] font-medium text-surface-300">
								{card.actor_name.charAt(0).toUpperCase()}
							</div>
							<span class="text-sm text-surface-300">{card.actor_name}</span>
						</div>
					{/if}

					<!-- Subject ID (e.g., PR-49, FERR-1056) -->
					{#if card.source_ref}
						<div class="mb-3 flex items-center gap-2">
							{#if card.source_url}
								<a
									href={card.source_url}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1.5 rounded-md border border-surface-700 px-3 py-1.5 text-xs text-surface-300 transition-colors hover:border-laya-orange/30 hover:text-laya-orange"
								>
									<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
										<path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
									</svg>
									{card.source_ref}
								</a>
							{:else}
								<span class="inline-flex items-center rounded-md border border-surface-700 px-3 py-1.5 text-xs font-medium text-surface-300">{card.source_ref}</span>
							{/if}
						</div>
					{/if}

					<!-- Header + summary -->
					<h2 class="mb-2 text-lg font-semibold text-surface-50">{card.header}</h2>
					<p class="mb-5 text-laya-base leading-relaxed text-surface-300">{card.summary}</p>

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
							{:else}
								<div class="prose-plan overflow-y-auto overflow-x-auto rounded-lg border border-surface-700 bg-surface-900/50 p-4 text-laya-base text-surface-200">
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
								{@const isTerminal = terminalStatuses.has(card.status)}
								{@const payload = action.payload}
								{@const editableField = payload ? getEditableTextField(payload) : null}

								<!-- Action payload preview with edit capability -->
								{#if editableField}
									{@const isEditing = editingActionId === action.action_id}
									<div class="mb-2 rounded-lg border border-surface-700 bg-surface-900/50 p-3">
										{#if !isEditing}
											<!-- Read-only view of editable fields -->
											{#each Object.entries(payload) as [key, value]}
												{#if typeof value === 'string' && value.length > 0 && key !== editableField}
													<div class="mb-1.5 flex items-center gap-1.5 text-[11px]">
														<span class="font-medium text-surface-500 capitalize">{key}:</span>
														<span class="text-surface-300">{value}</span>
													</div>
												{/if}
											{/each}
											<div class="max-h-48 overflow-y-auto whitespace-pre-wrap text-laya-base text-surface-200">{payload[editableField]}</div>
										{:else}
											<!-- Edit mode -->
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
										<!-- Edit / Save / Cancel controls -->
										{#if !isTerminal && !isSelected}
											<div class="mt-2 flex justify-end gap-3">
												{#if !isEditing}
													<button
														class="text-[11px] text-surface-400 hover:text-laya-orange transition-colors"
														onclick={() => startEditing(action)}
													>
														Edit draft
													</button>
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
														onclick={() => savePayload(card, action)}
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
												? 'border-laya-orange/50 bg-laya-orange/15 text-laya-orange'
												: card.selected_action_id && !isSelected
													? 'border-surface-700 bg-surface-800/50 text-surface-500'
													: 'border-surface-600 bg-surface-700/50 text-surface-200 hover:bg-surface-600'}
											{!isSelected && card.selected_action_id ? 'opacity-50' : ''}"
										onclick={() => executeAction(card, action.action_id)}
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
								</div>
							{/each}
							{#if executeError}
								<p class="mt-2 text-xs text-red-400">{executeError}</p>
							{/if}
						</div>
					{/if}

				</div>
			{/each}
		</div>

		<!-- Resize handle -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="group relative w-1.5 flex-shrink-0 cursor-col-resize bg-surface-700 hover:bg-laya-orange/40 transition-colors {dragging ? 'bg-laya-orange/50' : ''}"
			onmousedown={onDragStart}
		>
			<!-- Drag dots indicator — visible on hover -->
			<div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
				<div class="flex flex-col gap-1">
					<div class="h-0.5 w-0.5 rounded-full bg-surface-300"></div>
					<div class="h-0.5 w-0.5 rounded-full bg-surface-300"></div>
					<div class="h-0.5 w-0.5 rounded-full bg-surface-300"></div>
					<div class="h-0.5 w-0.5 rounded-full bg-surface-300"></div>
					<div class="h-0.5 w-0.5 rounded-full bg-surface-300"></div>
				</div>
			</div>
		</div>

		<!-- Right: Chat panel -->
		<div class="flex flex-col bg-surface-900/50" style="width: {chatWidth}px; min-width: {MIN_CHAT}px; max-width: {MAX_CHAT}px;">
			<!-- Chat header -->
			<div class="flex items-center gap-2 border-b border-surface-700 px-4 py-3">
				<svg class="h-4 w-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
				<span class="text-sm font-medium text-surface-200">
					{cards.length === 1 ? 'Chat about this card' : `Chat about these ${cards.length} cards`}
				</span>
			</div>

			<!-- Messages -->
			<div bind:this={messagesEl} class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
				{#if chatMessages.length === 0}
					<div class="flex h-full items-center justify-center">
						<p class="text-center text-xs text-surface-500 max-w-[240px]">
							{cards.length === 1
								? 'Ask anything about this card.'
								: `Ask anything about these ${cards.length} cards.`}
							<br />
							Laya has full context of {cards.length === 1 ? 'the' : 'all their'} intelligence, outputs, and metadata.
						</p>
					</div>
				{:else}
					{#each chatMessages as msg}
						<ChatMessage message={msg} />
					{/each}
					{#if chatSending}
						<div class="flex items-center gap-2 text-xs text-surface-500">
							<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
							</svg>
							Thinking...
						</div>
					{/if}
				{/if}
			</div>

			<!-- Input -->
			<div class="border-t border-surface-700 p-3">
				<div class="flex items-end gap-2">
					<textarea
						bind:this={textareaEl}
						bind:value={chatInput}
						oninput={resizeTextarea}
						onkeydown={handleKeydown}
						placeholder={cards.length === 1 ? 'Ask about this card...' : 'Ask about these cards...'}
						rows="1"
						class="flex-1 resize-none rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-laya-base text-surface-100 placeholder:text-surface-500 focus:border-laya-orange/50 focus:outline-none"
					></textarea>
					<button
						onclick={sendMessage}
						disabled={!chatInput.trim() || chatSending}
						aria-label="Send message"
						class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-laya-orange/80 text-surface-900 transition-colors hover:bg-laya-orange disabled:opacity-30 disabled:cursor-not-allowed"
					>
						<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path d="M5 12h14M12 5l7 7-7 7" />
						</svg>
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	/* Dark theme: subtle orange-tinted card background */
	:global([data-theme='dark']) .insight-card-bg {
		background-color: oklch(0.25 0.04 63 / 0.9);
	}
	/* Light theme: warm orange-tinted card background */
	:global([data-theme='light']) .insight-card-bg {
		background-color: oklch(0.93 0.035 63 / 0.7);
	}
</style>
