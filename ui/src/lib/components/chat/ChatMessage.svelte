<script lang="ts">
	import type { ChatMessage } from '$lib/api/types';

	let { message }: { message: ChatMessage } = $props();

	const isUser = $derived(message.role === 'user');
	const time = $derived(
		new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
	);

	// Replace [card:ID] and [event:ID] markers with clickable links
	function renderContent(text: string): string {
		return text
			.replace(
				/\[card:([^\]]+)\]/g,
				'<a href="/feed/$1" class="text-blue-400 underline hover:text-blue-300">card:$1</a>'
			)
			.replace(
				/\[event:([^\]]+)\]/g,
				'<span class="text-violet-400">event:$1</span>'
			);
	}
</script>

<div class="flex {isUser ? 'justify-end' : 'justify-start'}">
	<div
		class="max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm {isUser
			? 'bg-blue-600 text-white'
			: 'bg-surface-700 text-surface-200'}"
	>
		<!-- eslint-disable-next-line svelte/no-at-html-tags -->
		<div class="whitespace-pre-wrap break-words">{@html renderContent(message.content)}</div>
		<div
			class="mt-1 text-[10px] {isUser ? 'text-blue-200' : 'text-surface-500'}"
		>
			{time}
		</div>
	</div>
</div>
