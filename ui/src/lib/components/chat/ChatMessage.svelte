<script lang="ts">
	import type { ChatMessage } from '$lib/api/types';
	import { marked } from 'marked';
	import { goto } from '$app/navigation';
	import { pendingCardId } from '$lib/stores/chat';
	import { glassTheme } from '$lib/stores/glassTheme';

	let { message, streaming = false }: { message: ChatMessage; streaming?: boolean } = $props();

	let copied = $state(false);

	function copyResponse() {
		// Copy raw markdown content (strip thinking blocks)
		const content = parsed.response;
		if (!content) return;
		navigator.clipboard.writeText(content).then(() => {
			copied = true;
			setTimeout(() => (copied = false), 2000);
		});
	}

	const isUser = $derived(message.role === 'user');
	const time = $derived(
		new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
	);

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

	// Replace [card:ID] and [event:ID] markers with clickable links
	function replaceMarkers(text: string): string {
		return text
			.replace(
				/\[card:([^\]]+)\]/g,
				'<button data-card-link="$1" class="text-laya-orange underline hover:text-laya-peach cursor-pointer">card:$1</button>'
			)
			.replace(
				/\[event:([^\]]+)\]/g,
				'<span class="text-violet-400">event:$1</span>'
			);
	}

	function renderMarkdown(text: string): string {
		return replaceMarkers(marked(text) as string);
	}

	function handleClick(e: MouseEvent | KeyboardEvent) {
		if (e instanceof KeyboardEvent && e.key !== 'Enter') return;
		const target = (e.target as HTMLElement).closest('[data-card-link]') as HTMLElement | null;
		if (!target) return;
		const cardId = target.dataset.cardLink;
		if (!cardId) return;
		e.preventDefault();
		pendingCardId.set(cardId);
		goto('/feed');
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
			<div class="whitespace-pre-wrap break-words" onclick={handleClick} onkeydown={handleClick}>{@html replaceMarkers(message.content)}</div>
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
				<div class="prose-plan break-words" onclick={handleClick} onkeydown={handleClick}>{@html renderMarkdown(parsed.response)}{#if streaming}<span class="animate-pulse text-laya-orange">|</span>{/if}</div>
			{:else if streaming && !parsed.isThinking}
				<span class="inline-flex gap-1 text-surface-400">
					<span class="animate-bounce">.</span>
					<span class="animate-bounce" style="animation-delay: 0.1s">.</span>
					<span class="animate-bounce" style="animation-delay: 0.2s">.</span>
				</span>
			{/if}
		{/if}
		{#if !streaming}
			<div class="mt-1 flex items-center gap-2 text-[10px] {isUser ? 'text-laya-orange/60' : 'text-surface-500'}">
				<span>{time}</span>
				{#if !isUser && parsed.response}
					<span class="relative group/copy">
						<button
							onclick={copyResponse}
							class="opacity-0 group-hover:opacity-100 transition-opacity text-surface-500 hover:text-surface-300"
						>
							{#if copied}
								<svg class="h-3.5 w-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
							{:else}
								<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
							{/if}
						</button>
						<span class="pointer-events-none absolute left-1/2 bottom-full z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip {$glassTheme ? 'glass-tooltip-dense' : ''} px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/copy:opacity-100">
							{copied ? 'Copied!' : 'Copy response'}
						</span>
					</span>
				{/if}
			</div>
		{/if}
	</div>
</div>
