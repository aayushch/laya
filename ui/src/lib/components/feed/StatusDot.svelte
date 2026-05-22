<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	let { status, size = 'sm', errorMessage = '' }: { status: string; size?: 'sm' | 'md'; errorMessage?: string } = $props();

	// Status dot colors use custom CSS classes (status-dot-*) so they aren't
	// affected by the global light-theme text-color overrides in app.css, which
	// darken text-amber-400 / text-yellow-400 for readability on light
	// backgrounds — too dark for small iconic dots.
	const colors: Record<string, string> = {
		pending:            'status-dot-yellow',
		ready:              'status-dot-amber',
		agent_running:      'status-dot-violet',
		awaiting_input:     'status-dot-violet',
		done:               'status-dot-green',
		failed:             'status-dot-red',
		dismissed:          'text-surface-500',
		archived:           'text-surface-600',
	};

	const animate: Record<string, string> = {
		pending: 'animate-pulse',
		agent_running: 'animate-pulse',
	};

	const colorClass = $derived(colors[status] ?? 'text-surface-500');
	const animateClass = $derived(animate[status] ?? '');
	const sizeClass = $derived(size === 'md' ? 'h-[11px] w-[11px]' : 'h-2 w-2');
	const hasError = $derived(status === 'failed' && !!errorMessage);
</script>

{#snippet dot()}
<!-- Each status gets a unique shape so meaning isn't conveyed by color alone -->
<svg class="{sizeClass} shrink-0 {colorClass} {animateClass}" viewBox="0 0 8 8" fill="none" aria-hidden="true">
	{#if status === 'done'}
		<!-- Checkmark -->
		<path d="M1.5 4.5 L3 6 L6.5 2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
	{:else if status === 'failed'}
		<!-- X mark -->
		<path d="M2 2 L6 6 M6 2 L2 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	{:else if status === 'agent_running'}
		<!-- Filled circle (pulsing) -->
		<circle cx="4" cy="4" r="3" fill="currentColor" />
	{:else if status === 'awaiting_input'}
		<!-- Triangle / warning -->
		<path d="M4 1 L7 6.5 L1 6.5 Z" fill="currentColor" />
	{:else if status === 'pending'}
		<!-- Half-filled circle (processing) -->
		<circle cx="4" cy="4" r="2.5" stroke="currentColor" stroke-width="1.2" />
		<path d="M4 1.5 A2.5 2.5 0 0 1 4 6.5 Z" fill="currentColor" />
	{:else if status === 'ready'}
		<!-- Filled circle -->
		<circle cx="4" cy="4" r="3" fill="currentColor" />
	{:else if status === 'dismissed'}
		<!-- Dash / minus -->
		<path d="M1.5 4 L6.5 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	{:else if status === 'archived'}
		<!-- Small square -->
		<rect x="1.5" y="1.5" width="5" height="5" rx="0.5" stroke="currentColor" stroke-width="1.2" />
	{:else}
		<!-- Fallback: filled circle -->
		<circle cx="4" cy="4" r="3" fill="currentColor" />
	{/if}
</svg>
{/snippet}

{#if hasError}
	<span class="inline-flex cursor-help" title={errorMessage}>{@render dot()}</span>
{:else}
	{@render dot()}
{/if}
