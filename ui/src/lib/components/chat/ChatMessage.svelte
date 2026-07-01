<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { ChatMessage } from '$lib/api/types';
	import { parseBackendDate } from '$lib/utils/datetime';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';
	import { goto } from '$app/navigation';
	import { pendingCardId } from '$lib/stores/chat';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';

	let { message, streaming = false }: { message: ChatMessage; streaming?: boolean } = $props();

	let copied = $state(false);
	let fixedTooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showTooltip(el: HTMLElement, text: string) {
		const rect = el.getBoundingClientRect();
		fixedTooltip = { text, top: rect.top - 4, left: rect.left + rect.width / 2 };
	}
	function hideTooltip() { fixedTooltip = null; }

	function copyResponse() {
		// Copy raw markdown content (strip thinking blocks)
		const content = parsed.response;
		if (!content) return;
		navigator.clipboard.writeText(content).then(() => {
			copied = true;
			if (fixedTooltip) fixedTooltip = { ...fixedTooltip, text: 'Copied!' };
			setTimeout(() => {
				copied = false;
				if (fixedTooltip) fixedTooltip = { ...fixedTooltip, text: 'Copy response' };
			}, 2000);
		});
	}

	const isUser = $derived(message.role === 'user');
	const time = $derived.by(() => {
		const d = parseBackendDate(message.timestamp);
		if (!d) return '';
		const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
		const now = new Date();
		const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
		const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
		const diff = today.getTime() - msgDay.getTime();
		if (diff === 0) return hm;
		if (diff === 86400000) return `Yesterday ${hm}`;
		return `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${hm}`;
	});

	// Parse <think>...</think> blocks from assistant content
	const parsed = $derived.by(() => {
		const content = message.content;
		if (isUser) return { thinking: null, response: content, isThinking: false };

		// Complete thinking block — <think>...</think> present
		const match = content.match(/<think>([\s\S]*?)<\/think>/);
		if (match) {
			return {
				thinking: match[1].trim(),
				response: content.slice(match.index! + match[0].length).trim(),
				isThinking: false
			};
		}

		// Open <think> tag with no closing — still thinking, or model failed to close
		if (content.includes('<think>')) {
			if (streaming) {
				// Still streaming — show thinking in progress
				return {
					thinking: content.replace('<think>', '').trim(),
					response: '',
					isThinking: true
				};
			}
			// Streaming done but no closing tag — treat as normal response
			return { thinking: null, response: content.replace('<think>', ''), isThinking: false };
		}

		return { thinking: null, response: content, isThinking: false };
	});

	// Inline card glyph — the link renders this icon rather than the raw id, which
	// is noisy in prose. The id is revealed on hover via the laya tooltip.
	const CARD_ICON =
		'<svg class="inline-block h-3.5 w-3.5 align-text-bottom" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';

	function cardButton(id: string): string {
		return `<button data-card-link="${id}" aria-label="Open card ${id}" class="inline-flex items-center align-text-bottom text-laya-orange hover:text-laya-peach cursor-pointer">${CARD_ICON}</button>`;
	}

	// Linkify card references. We match two forms in a single pass: the explicit
	// [card:ID] marker the prompt asks for, AND bare card_<hex> ids — the LLM often
	// emits the bare id (e.g. in a bold list) instead of wrapping it, so without
	// this they'd render as plain text. The alternation consumes a whole [card:...]
	// marker before the bare-id branch can see its inner id, so each reference is
	// linkified exactly once. Card ids are card_ + 12 hex chars (engine queue.py).
	// Note: linkification is by shape only — the click handler validates the card
	// actually exists before navigating, so a stale/look-alike id fails gracefully.
	function replaceMarkers(text: string): string {
		return text
			.replace(/\[card:([^\]]+)\]|\bcard_[0-9a-f]{12}\b/g, (full, bracketId) =>
				cardButton(bracketId ?? full)
			)
			.replace(
				/\[event:([^\]]+)\]/g,
				'<span class="text-violet-400">event:$1</span>'
			);
	}

	function renderMarkdown(text: string): string {
		return DOMPurify.sanitize(replaceMarkers(marked(text) as string));
	}

	// Tracks the card id whose tooltip is currently shown on hover, so we don't
	// re-trigger showTooltip on every pointer move.
	let hoverCardId = $state<string | null>(null);
	// While set (a timestamp in the future), a "Card not found" flash is showing
	// and hover must not overwrite it.
	let flashUntil = 0;

	async function handleClick(e: MouseEvent | KeyboardEvent) {
		if (e instanceof KeyboardEvent && e.key !== 'Enter') return;
		const target = (e.target as HTMLElement).closest('[data-card-link]') as HTMLElement | null;
		if (!target) return;
		const cardId = target.dataset.cardLink;
		if (!cardId) return;
		e.preventDefault();
		// Feed normalizes a missing prefix; mirror that when validating.
		const fullId = cardId.startsWith('card_') ? cardId : `card_${cardId}`;
		try {
			await engineApi.getCard(fullId);
		} catch {
			// Look-alike / stale id — surface it instead of navigating to a dead view.
			flashUntil = Date.now() + 2000;
			hoverCardId = null;
			showTooltip(target, 'Card not found');
			setTimeout(() => {
				if (Date.now() >= flashUntil) hideTooltip();
			}, 2000);
			return;
		}
		pendingCardId.set(cardId);
		goto('/feed');
	}

	// Hover over a card link → show its id in the laya tooltip. Uses event
	// delegation on the message container because the links are injected as raw HTML.
	function handleHover(e: PointerEvent) {
		if (Date.now() < flashUntil) return;
		const link = (e.target as HTMLElement).closest?.('[data-card-link]') as HTMLElement | null;
		if (link) {
			const id = link.dataset.cardLink;
			if (id && hoverCardId !== id) {
				hoverCardId = id;
				showTooltip(link, id);
			}
		} else if (hoverCardId) {
			hoverCardId = null;
			hideTooltip();
		}
	}

	function handleHoverLeave() {
		if (Date.now() < flashUntil) return;
		if (hoverCardId) {
			hoverCardId = null;
			hideTooltip();
		}
	}
</script>

<div class="flex {isUser ? 'justify-end' : 'justify-start'}">
	<div
		class="group max-w-[95%] rounded-xl px-3.5 py-2.5 text-laya-base {isUser
			? 'bg-laya-orange/20 text-surface-100 ring-1 ring-laya-orange/30'
			: $glassTheme ? 'bg-white/[0.06] text-surface-200 ring-1 ring-white/[0.06]' : 'bg-surface-700 text-surface-200'}"
	>
		{#if isUser}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="whitespace-pre-wrap break-words" onclick={handleClick} onkeydown={handleClick} onpointerover={handleHover} onpointerleave={handleHoverLeave}>{@html DOMPurify.sanitize(replaceMarkers(message.content))}</div>
		{:else}
			<!-- Thinking indicator / collapsible block -->
			{#if parsed.isThinking}
				<div class="chat-thinking mb-2">
					<div class="flex items-center gap-1.5 text-laya-micro font-medium text-surface-400">
						<svg class="h-3 w-3 animate-spin text-surface-500" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
						</svg>
						Thinking...
					</div>
					{#if parsed.thinking}
						<div class="mt-1.5 border-l-2 border-surface-600 pl-2.5 text-laya-micro leading-relaxed text-surface-500 whitespace-pre-wrap">
							{parsed.thinking}<span class="animate-pulse text-laya-orange">|</span>
						</div>
					{/if}
				</div>
			{:else if parsed.thinking}
				<details class="chat-thinking mb-2">
					<summary class="cursor-pointer text-laya-micro font-medium text-surface-500 hover:text-surface-300 select-none">
						Thought process
					</summary>
					<div class="mt-1.5 border-l-2 border-surface-600 pl-2.5 text-laya-micro leading-relaxed text-surface-500 whitespace-pre-wrap">
						{parsed.thinking}
					</div>
				</details>
			{/if}

			<!-- Main response -->
			{#if parsed.response}
				<!-- eslint-disable-next-line svelte/no-at-html-tags -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="prose-plan break-words" onclick={handleClick} onkeydown={handleClick} onpointerover={handleHover} onpointerleave={handleHoverLeave}>{@html renderMarkdown(parsed.response)}{#if streaming}<span class="animate-pulse text-laya-orange">|</span>{/if}</div>
			{:else if streaming && !parsed.isThinking}
				<span class="inline-flex gap-1 text-surface-400">
					<span class="animate-bounce">.</span>
					<span class="animate-bounce" style="animation-delay: 0.1s">.</span>
					<span class="animate-bounce" style="animation-delay: 0.2s">.</span>
				</span>
			{/if}
		{/if}
		{#if !streaming}
			<div class="mt-1 flex items-center gap-2 text-laya-micro {isUser ? 'text-laya-orange/60' : 'text-surface-500'}">
				<span>{time}</span>
				{#if !isUser && parsed.response}
					<button
						onclick={copyResponse}
						onmouseenter={(e) => showTooltip(e.currentTarget, copied ? 'Copied!' : 'Copy response')}
						onmouseleave={hideTooltip}
						class="opacity-0 group-hover:opacity-100 transition-opacity text-surface-500 hover:text-surface-300"
					>
						{#if copied}
							<svg class="h-3.5 w-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
						{:else}
							<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
						{/if}
					</button>
				{/if}
			</div>
		{/if}
	</div>
</div>

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {fixedTooltip.top}px; left: {fixedTooltip.left}px; translate: -50% -100%;"
	>
		{fixedTooltip.text}
	</span>
{/if}
