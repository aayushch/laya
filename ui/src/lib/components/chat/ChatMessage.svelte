<script lang="ts">
	import type { ChatMessage } from '$lib/api/types';
	import { marked } from 'marked';

	let { message, streaming = false }: { message: ChatMessage; streaming?: boolean } = $props();

	const isUser = $derived(message.role === 'user');
	const time = $derived(
		new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
	);

	// Replace [card:ID] and [event:ID] markers with clickable links
	function replaceMarkers(text: string): string {
		return text
			.replace(
				/\[card:([^\]]+)\]/g,
				'<a href="/feed/$1" class="text-laya-orange underline hover:text-laya-peach">card:$1</a>'
			)
			.replace(
				/\[event:([^\]]+)\]/g,
				'<span class="text-violet-400">event:$1</span>'
			);
	}

	function renderContent(text: string): string {
		if (isUser) {
			return replaceMarkers(text);
		}
		// Render markdown for assistant messages, then replace card/event markers
		return replaceMarkers(marked(text) as string);
	}
</script>

<div class="flex {isUser ? 'justify-end' : 'justify-start'}">
	<div
		class="max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm {isUser
			? 'bg-laya-orange/20 text-surface-100 ring-1 ring-laya-orange/30'
			: 'bg-surface-700 text-surface-200'}"
	>
		<!-- eslint-disable-next-line svelte/no-at-html-tags -->
		<div class="{isUser ? 'whitespace-pre-wrap' : 'prose-plan'} break-words">{@html renderContent(message.content)}{#if streaming && message.content}<span class="animate-pulse text-laya-orange">|</span>{/if}</div>
		{#if !streaming}
			<div
				class="mt-1 text-[10px] {isUser ? 'text-laya-orange/60' : 'text-surface-500'}"
			>
				{time}
			</div>
		{/if}
	</div>
</div>
