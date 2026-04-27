<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { ActionCard, ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from '$lib/components/chat/ChatMessage.svelte';
	import { marked } from 'marked';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { lastMessage } from '$lib/stores/websocket';

	let cards = $state<ActionCard[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Action execution & draft editing state
	let executingActionId = $state<string | null>(null);
	let executeError = $state<string | null>(null);
	let editingActionId = $state<string | null>(null);
	let editedPayload = $state<Record<string, string>>({});
	let savingPayload = $state(false);

	// Polish-in-flight state — mirrors CardDetail's pattern. Spinner is seeded from
	// persisted `_polishing` payload flags (set by the engine while the LLM is
	// rewriting); `lastMessage` keeps it in sync as start/complete events stream in.
	let polishingActionIds = $state(new Set<string>());
	let polishErrors = $state<Record<string, string>>({});
	let _polishSeededIds = new Set<string>();

	const terminalStatuses = new Set(['done', 'failed', 'dismissed', 'archived']);

	function formatCardDate(dateStr?: string): string {
		if (!dateStr) return '';
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const d = new Date(utcStr);
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
	}

	/** Identify the main editable text field in an action payload. */
	function getEditableTextField(payload: Record<string, unknown>): string | null {
		for (const key of ['body', 'comment', 'message', 'description']) {
			if (typeof payload[key] === 'string' && (payload[key] as string).length > 0) return key;
		}
		return null;
	}

	function startEditing(
		action: { action_id: string; payload: Record<string, unknown> },
		fallbackBody?: string
	) {
		editingActionId = action.action_id;
		const p = action.payload;
		editedPayload = {};
		for (const [key, value] of Object.entries(p)) {
			if (typeof value === 'string' && value.length > 0) {
				editedPayload[key] = value;
			}
		}
		// When the engine couldn't parse the LLM's payload (e.g. payload arrived as
		// `{raw: "subject"}` because the model emitted a non-JSON string), the action
		// has no body/comment field but the actual draft text still lives in
		// staged_output.content. Seed `body` so the user can still edit + save and the
		// next executor pass has something usable to send.
		if (fallbackBody && !editedPayload.body && !editedPayload.comment && !editedPayload.message && !editedPayload.description) {
			editedPayload.body = fallbackBody;
		}
	}

	async function savePayload(card: ActionCard, action: { action_id: string; payload: Record<string, unknown> }) {
		savingPayload = true;
		try {
			await engineApi.updateActionPayload(card.card_id, action.action_id, editedPayload);
			Object.assign(action.payload, editedPayload);
			// Flip `_edited` locally so the Polish button shows immediately;
			// the WS echo will confirm shortly.
			action.payload._edited = true;
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Failed to save draft';
		} finally {
			savingPayload = false;
		}
	}

	async function polishDraft(card: ActionCard, action: { action_id: string }) {
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

	// Seed polish state from persisted `_polishing` flags whenever cards load
	// or change. Tracks per-action seed so we don't wipe client-side errors
	// from failed API calls on subsequent payload mutations.
	$effect(() => {
		for (const card of cards) {
			for (const a of card.suggested_actions ?? []) {
				if (_polishSeededIds.has(a.action_id)) continue;
				_polishSeededIds.add(a.action_id);
				const p = a.payload as Record<string, unknown> | undefined;
				if (p?._polishing === true) {
					polishingActionIds = new Set([...polishingActionIds, a.action_id]);
				}
				if (typeof p?._polish_error === 'string') {
					polishErrors = { ...polishErrors, [a.action_id]: p._polish_error as string };
				}
			}
		}
	});

	// React to per-action payload updates streamed via WebSocket. Body runs in
	// untrack() so the effect depends only on $lastMessage — without it, reading
	// card.suggested_actions / action.payload tracks the reactive proxies and
	// re-triggers the same effect on every payload write, freezing the UI.
	$effect(() => {
		const msg = $lastMessage;
		if (!msg || msg.type !== 'action_payload_updated') return;
		untrack(() => {
			const cardId = (msg as { card_id?: string }).card_id;
			const card = cards.find(c => c.card_id === cardId);
			if (!card) return;
			const actionId = (msg as { action_id?: string }).action_id;
			const newPayload = (msg.payload as { payload?: Record<string, unknown> })?.payload;
			if (!actionId || !newPayload) return;
			// Mirror payload changes onto the local card so the read-only view
			// reflects the polished/edited content without a full reload.
			const action = card.suggested_actions?.find(a => a.action_id === actionId);
			if (action) Object.assign(action.payload, newPayload);
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

	// MEDIUM is the only priority pill that uses a solid surface fill rather than a
	// translucent accent; in glass mode that flat grey breaks the frosted look, so
	// swap it for a white-overlay translucent equivalent.
	const priorityColors = $derived<Record<string, string>>({
		CRITICAL: 'bg-red-500/20 text-red-400',
		HIGH: 'bg-orange-500/20 text-orange-400',
		MEDIUM: $glassTheme ? 'bg-white/[0.08] text-surface-300' : 'bg-surface-700 text-surface-300',
		LOW: 'bg-laya-gold/25 text-laya-amber'
	});

	onMount(async () => {
		await loadCards();
		// Restore the prior conversation for this exact card set (if any).
		// Runs after loadCards so we don't block the visible card render.
		await restoreChat();
	});

	async function loadCards() {
		loading = true;
		error = null;
		try {
			const results = await Promise.allSettled(cardIds.map((id) => engineApi.getCard(id)));
			cards = results
				.filter((r): r is PromiseFulfilledResult<ActionCard> => r.status === 'fulfilled')
				.map((r) => r.value)
				.sort((a, b) => {
					const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
					const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
					return tb - ta;
				});
			if (cards.length === 0) {
				error = 'None of the referenced cards could be found';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load cards';
		} finally {
			loading = false;
		}
	}

	/** Look up the conversation anchored to this card set and load its messages. */
	async function restoreChat() {
		if (cardIds.length === 0) return;
		try {
			const conv = await engineApi.getConversationByCards(cardIds);
			if (!conv) return;
			chatConversationId = conv.conversation_id;
			const msgs = await engineApi.getConversationMessages(conv.conversation_id, 100);
			// Backend returns newest-first; reverse to display chronologically
			chatMessages = [...msgs].reverse();
			scrollToBottom();
		} catch {
			// Silently ignore — the user can still start a fresh chat
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
				buildCardContext(),
				cardIds
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
			<!-- Back button — glass-hover swaps the solid surface-800 hover for a
			     theme-aware translucent overlay so it doesn't punch a solid rect into
			     the frosted background. -->
			<div class="mb-5 flex items-center gap-3">
				<button
					onclick={goBack}
					class="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-surface-400 transition-colors hover:text-surface-200 {$glassTheme ? 'glass-hover' : 'hover:bg-surface-800'}"
				>
					<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path d="M15 19l-7-7 7-7" />
					</svg>
					Omni
				</button>
				{#if cards.length > 1}
					<div class="h-4 w-px {$glassTheme ? 'bg-white/[0.12]' : 'bg-surface-700'}"></div>
					<span class="text-xs text-surface-400">{cards.length} related cards</span>
				{/if}
			</div>

			<!-- Render each card. Glass theme swaps the solid orange-tinted bg for a
			     translucent frosted-glass surface so the card sits on the glass page
			     instead of overlaying it with a flat fill. -->
			{#each cards as card, idx}
				{@const draftAction = card.suggested_actions?.find((a) => a.payload && getEditableTextField(a.payload as Record<string, unknown>))
					?? (card.staged_output?.type === 'draft_reply' ? (card.suggested_actions?.[0] ?? null) : null)}
				<div class="mb-4 rounded-xl p-5 {$glassTheme ? 'glass-card bg-white/[0.04] border border-white/[0.08]' : 'insight-card-bg'}">
					<!-- Card meta bar -->
					<div class="mb-3 flex items-center gap-2 flex-wrap">
						<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider {priorityColors[card.priority]}">{card.priority}</span>
						<span class="text-[10px] text-surface-500 uppercase tracking-wider">{card.persona}</span>
						<span class="text-[10px] text-surface-500 uppercase tracking-wider">{card.category}</span>
						{#if card.entity_id}
							<span class="text-[10px] text-surface-500 uppercase tracking-wider">{extractPlatform(card)}</span>
						{/if}
						<span class="rounded px-1.5 py-0.5 text-[10px] text-surface-400 {$glassTheme ? 'border border-white/[0.06] bg-white/[0.04]' : 'bg-surface-800'}">{card.status}</span>
						{#if card.created_at}
							<span class="ml-auto shrink-0 text-[10px] text-surface-500">{formatCardDate(card.created_at)}</span>
						{/if}
					</div>

					<!-- Actor -->
					{#if card.actor_name}
						<div class="mb-3 flex items-center gap-2">
							<div class="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-medium text-surface-300 {$glassTheme ? 'omni-glass-chip' : 'bg-surface-700'}">
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
									class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs text-surface-300 transition-colors hover:border-laya-orange/30 hover:text-laya-orange {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-700'}"
								>
									<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
										<path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
									</svg>
									{card.source_ref}
								</a>
							{:else}
								<span class="inline-flex items-center rounded-md border px-3 py-1.5 text-xs font-medium text-surface-300 {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-700'}">{card.source_ref}</span>
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

					<!-- Editable draft preview snippet — same look + Edit/Polish controls
					     as CardDetail. Used both in place of the staged_output markdown
					     blob (for draft_reply) and in the suggested-actions section
					     (for action-only cards without staged_output). -->
					{#snippet draftPreview(action: NonNullable<ActionCard['suggested_actions']>[number])}
						{@const payload = action.payload as Record<string, unknown>}
						{@const detectedField = getEditableTextField(payload)}
						{@const isDraftReply = card.staged_output?.type === 'draft_reply'}
						{@const fallbackText = isDraftReply ? (card.staged_output?.content ?? '') : ''}
						{@const editableField = detectedField ?? (fallbackText ? 'body' : null)}
						{@const displayText = (detectedField ? (payload[detectedField] as string) : fallbackText) ?? ''}
						{#if editableField && displayText}
							{@const isEditing = editingActionId === action.action_id}
							{@const isPolishing = polishingActionIds.has(action.action_id)}
							{@const hasEdits = payload._edited === true}
							{@const polishErrorMsg = polishErrors[action.action_id]}
							{@const isTerminal = terminalStatuses.has(card.status)}
							<div class="relative rounded-lg p-3 {$glassTheme ? 'omni-glass-panel' : 'border border-surface-700 bg-surface-900/50'}">
								{#if !isEditing}
									{#each Object.entries(payload) as [key, value]}
										{#if !key.startsWith('_') && typeof value === 'string' && value.length > 0 && key !== editableField && key !== 'raw'}
											<div class="mb-1.5 flex items-center gap-1.5 text-[11px]">
												<span class="font-medium text-surface-500 capitalize">{key}:</span>
												<span class="text-surface-300">{value}</span>
											</div>
										{/if}
									{/each}
									<div class="max-h-96 overflow-y-auto whitespace-pre-wrap text-laya-base text-surface-200">{displayText}</div>
								{:else}
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
										rows="8"
										bind:value={editedPayload[editableField]}
									></textarea>
								{/if}
								{#if isPolishing}
									<div class="pointer-events-auto absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg bg-surface-900/70 backdrop-blur-sm">
										<svg class="h-6 w-6 animate-spin text-laya-orange" fill="none" viewBox="0 0 24 24">
											<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
											<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
										</svg>
										<span class="text-[11px] font-medium text-laya-orange">Polishing draft…</span>
									</div>
								{/if}
								{#if !isTerminal}
									<div class="mt-2 flex items-center justify-end gap-3">
										{#if polishErrorMsg && !isPolishing}
											<span class="mr-auto text-[11px] text-red-400">{polishErrorMsg}</span>
										{/if}
										{#if !isEditing}
											<button
												class="text-[11px] text-surface-400 hover:text-laya-orange transition-colors disabled:opacity-40 disabled:hover:text-surface-400"
												onclick={() => startEditing(action, detectedField ? undefined : fallbackText)}
												disabled={isPolishing}
											>
												Edit draft
											</button>
											{#if hasEdits}
												<button
													class="inline-flex items-center gap-1 text-[11px] font-medium text-laya-gold hover:text-laya-peach transition-colors disabled:opacity-40 disabled:hover:text-laya-gold"
													onclick={() => polishDraft(card, action)}
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
					{/snippet}

					<!-- Staged output. For draft_reply we render the editable preview
					     in place of the read-only markdown so Edit/Polish controls land
					     where the user expects them. -->
					{#if card.staged_output}
						<div class="mb-5">
							<h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-surface-400">
								{outputTypeLabels[card.staged_output.type] ?? 'Output'}
							</h3>
							{#if card.staged_output.type === 'code_fix'}
								<pre class="overflow-x-auto rounded-lg p-3 text-xs text-surface-200 {$glassTheme ? 'omni-glass-panel' : 'bg-surface-900'}">{card.staged_output.content}</pre>
							{:else if card.staged_output.type === 'draft_reply' && draftAction}
								{@render draftPreview(draftAction)}
							{:else}
								<div class="prose-plan overflow-y-auto overflow-x-auto rounded-lg p-4 text-laya-base text-surface-200 {$glassTheme ? 'omni-glass-panel' : 'border border-surface-700 bg-surface-900/50'}">
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

								<!-- Editable preview only when staged_output (draft_reply) hasn't already
								     consumed this action — avoids rendering the same draft twice. -->
								{#if card.staged_output?.type !== 'draft_reply' || action.action_id !== draftAction?.action_id}
									<div class="mb-2">
										{@render draftPreview(action)}
									</div>
								{/if}

								<div class="mb-2 flex flex-wrap gap-2">
									<button
										class="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed
											{isSelected
												? 'border-laya-orange/50 bg-laya-orange/15 text-laya-orange'
												: card.selected_action_id && !isSelected
													? ($glassTheme ? 'omni-glass-button-muted text-surface-500' : 'border-surface-700 bg-surface-800/50 text-surface-500')
													: ($glassTheme ? 'omni-glass-button text-surface-200' : 'border-surface-600 bg-surface-700/50 text-surface-200 hover:bg-surface-600')}
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

		<!-- Resize handle — invisible, only shows drag dots on hover -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="group relative w-3 flex-shrink-0 cursor-col-resize"
			onmousedown={onDragStart}
		>
			<div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
				<div class="flex flex-col gap-1">
					<div class="h-1 w-1 rounded-full bg-surface-400"></div>
					<div class="h-1 w-1 rounded-full bg-surface-400"></div>
					<div class="h-1 w-1 rounded-full bg-surface-400"></div>
				</div>
			</div>
		</div>

		<!-- Right: Chat panel — rounded container aligned with cards -->
		<div class="flex flex-col my-6 mr-6 rounded-xl overflow-hidden {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-800/50'}" style="width: {chatWidth}px; min-width: {MIN_CHAT}px; max-width: {MAX_CHAT}px;">
			<!-- Chat header -->
			<div class="flex items-center gap-2 px-4 py-3">
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
			<div class="p-3">
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

	/* Glass-mode small chip (e.g. actor avatar). Uses a white tint on dark glass and
	   a black tint on light glass — bg-white/[X] would be invisible on cream. */
	:global([data-theme='dark']) .omni-glass-chip {
		background-color: rgb(255 255 255 / 0.12);
		border: 1px solid rgb(255 255 255 / 0.10);
	}
	:global([data-theme='light']) .omni-glass-chip {
		background-color: rgb(0 0 0 / 0.10);
		border: 1px solid rgb(0 0 0 / 0.12);
	}

	/* Glass-mode framed panel (e.g. draft preview). Light theme needs a stronger
	   tint to read as a distinct surface against the warm card background. */
	:global([data-theme='dark']) .omni-glass-panel {
		background-color: rgb(255 255 255 / 0.05);
		border: 1px solid rgb(255 255 255 / 0.08);
	}
	:global([data-theme='light']) .omni-glass-panel {
		background-color: rgb(0 0 0 / 0.04);
		border: 1px solid rgb(0 0 0 / 0.10);
	}

	/* Glass-mode action button. White-tint on dark, black-tint on light so the
	   "Reply to ..." pill stays visible against either theme. */
	:global([data-theme='dark']) .omni-glass-button {
		background-color: rgb(255 255 255 / 0.06);
		border-color: rgb(255 255 255 / 0.12);
	}
	:global([data-theme='dark']) .omni-glass-button:hover {
		background-color: rgb(255 255 255 / 0.10);
	}
	:global([data-theme='light']) .omni-glass-button {
		background-color: rgb(0 0 0 / 0.06);
		border-color: rgb(0 0 0 / 0.14);
	}
	:global([data-theme='light']) .omni-glass-button:hover {
		background-color: rgb(0 0 0 / 0.10);
	}
	:global([data-theme='dark']) .omni-glass-button-muted {
		background-color: rgb(255 255 255 / 0.02);
		border-color: rgb(255 255 255 / 0.06);
	}
	:global([data-theme='light']) .omni-glass-button-muted {
		background-color: rgb(0 0 0 / 0.03);
		border-color: rgb(0 0 0 / 0.08);
	}
</style>
