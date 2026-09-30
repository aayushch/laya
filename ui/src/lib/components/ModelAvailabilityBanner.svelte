<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { health } from '$lib/stores/health';
	import { engineApi } from '$lib/api/engine';
	import { bannerText, fixHref, fixLabel, modelProblems } from '$lib/utils/modelAvailability';

	let retrying = $state(false);
	const problems = $derived(modelProblems($health));
	const text = $derived(bannerText($health));

	/** Re-queue held events; the WS push then updates the banner. */
	async function retry(): Promise<void> {
		retrying = true;
		try {
			await engineApi.retryHeldEvents();
		} catch {
			// Engine unreachable — the banner stays until the next health poll.
		} finally {
			retrying = false;
		}
	}
</script>

{#if text}
	<div class="flex items-center justify-center gap-2 bg-red-500/15 border-b border-red-500/30 px-4 py-1.5">
		<svg class="h-3.5 w-3.5 text-red-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
		</svg>
		<span class="text-xs text-red-300" title={problems[0]?.reason ?? ''}>{text}</span>
		{#if problems[0]}
			<a href={fixHref(problems[0])} class="ml-1 text-xs font-medium text-red-400 underline underline-offset-2 hover:text-red-300">{fixLabel(problems[0])}</a>
		{/if}
		<button
			onclick={retry}
			disabled={retrying}
			class="ml-1 text-xs font-medium text-red-400 underline underline-offset-2 hover:text-red-300 disabled:opacity-50"
		>{retrying ? 'Retrying…' : 'Try again'}</button>
	</div>
{/if}
