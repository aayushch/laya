<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { EgressPreviewResponse } from '$lib/api/types';

	let {
		preview,
		onConfirm,
		onCancel,
		loading = false
	}: {
		preview: EgressPreviewResponse;
		onConfirm: () => void;
		onCancel: () => void;
		loading: boolean;
	} = $props();

	const platformIcons: Record<string, string> = {
		gmail: 'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
		slack: 'M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5zm0 0H20m-9.5 0c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5S9 17.33 9 16.5v-5c0-.83.67-1.5 1.5-1.5zm0 0H4',
		jira: 'M12 2L2 12l10 10 10-10L12 2z',
		github: 'M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 00-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0020 4.77 5.07 5.07 0 0019.91 1S18.73.65 16 2.48a13.38 13.38 0 00-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 005 4.77a5.44 5.44 0 00-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 009 18.13V22',
		bitbucket: 'M12 2L2 12l10 10 10-10L12 2z'
	};

	const detailEntries = $derived(
		Object.entries(preview.details).filter(
			([, v]) => v !== null && v !== undefined && v !== ''
		)
	);

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onCancel();
	}

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onCancel();
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
	role="dialog"
	aria-label="Confirm action"
	tabindex="0"
	onclick={handleBackdrop}
	onkeydown={handleKeydown}
>
	<div class="mx-4 w-full max-w-md rounded-xl border border-surface-700 bg-surface-900 p-5 shadow-2xl">
		<!-- Header -->
		<div class="mb-4 flex items-center gap-3">
			<div class="rounded-lg bg-surface-800 p-2">
				<svg class="h-5 w-5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={platformIcons[preview.platform] ?? platformIcons.github} />
				</svg>
			</div>
			<div>
				<h3 class="text-sm font-semibold text-surface-50">{preview.summary}</h3>
				<p class="text-xs text-surface-400">{preview.platform} / {preview.action_type}</p>
			</div>
		</div>

		<!-- Details -->
		{#if detailEntries.length > 0}
			<div class="mb-4 space-y-1.5 rounded-lg border border-surface-700 bg-surface-800/50 p-3">
				{#each detailEntries as [key, value]}
					<div class="flex items-start gap-2 text-xs">
						<span class="w-24 flex-shrink-0 font-medium text-surface-400">{key}</span>
						<span class="text-surface-200 break-all">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
					</div>
				{/each}
			</div>
		{/if}

		<!-- Warnings -->
		{#if preview.warnings.length > 0}
			<div class="mb-4 space-y-1">
				{#each preview.warnings as warning}
					<div class="flex items-start gap-2 rounded-md bg-laya-amber/10 border border-laya-amber/20 px-3 py-2">
						<svg class="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-laya-amber" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
						</svg>
						<span class="text-xs text-laya-amber">{warning}</span>
					</div>
				{/each}
			</div>
		{/if}

		<!-- Estimated impact -->
		{#if preview.estimated_impact}
			<p class="mb-4 text-xs text-surface-400">{preview.estimated_impact}</p>
		{/if}

		<!-- Buttons -->
		<div class="flex justify-end gap-2">
			<button
				class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
				onclick={onCancel}
				disabled={loading}
			>
				Cancel
			</button>
			<button
				class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-3 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50 disabled:cursor-not-allowed"
				onclick={onConfirm}
				disabled={loading}
			>
				{#if loading}
					<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
					</svg>
					Executing...
				{:else}
					Confirm
				{/if}
			</button>
		</div>
	</div>
</div>
