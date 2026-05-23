<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	let {
		content,
		copyText,
		class: classProp = '',
		showCopy = true,
	}: {
		content: string;
		copyText?: string;
		class?: string;
		showCopy?: boolean;
	} = $props();

	let copied = $state(false);

	const html = $derived(DOMPurify.sanitize(marked(content) as string));

	function handleCopy() {
		const text = copyText ?? content;
		navigator.clipboard.writeText(text).then(() => {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		});
	}
</script>

{#if showCopy}
	<!-- Outer wrapper holds the absolute copy button anchored to top-right.
	     Keeping the scroll/padding/border on the inner prose-plan div means
	     the button stays at the visible top edge regardless of scroll. -->
	<div class="group relative">
		<div class="prose-plan {classProp}">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -->
			{@html html}
		</div>
		<button
			type="button"
			class="absolute right-2 top-2 z-10 rounded-md p-1 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 {copied ? 'bg-surface-900/70 text-green-400' : 'bg-surface-900/70 text-surface-400 hover:bg-surface-800/80 hover:text-surface-200'}"
			onclick={handleCopy}
			aria-label={copied ? 'Copied' : 'Copy markdown'}
			title={copied ? 'Copied!' : 'Copy markdown'}
		>
			{#if copied}
				<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
				</svg>
			{:else}
				<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
				</svg>
			{/if}
		</button>
	</div>
{:else}
	<div class="prose-plan {classProp}">
		<!-- eslint-disable-next-line svelte/no-at-html-tags -->
		{@html html}
	</div>
{/if}
