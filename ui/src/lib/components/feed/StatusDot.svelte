<script lang="ts">
	let { status, size = 'sm' }: { status: string; size?: 'sm' | 'md' } = $props();

	// Luminance-separated colors: done is lighter, failed is darker
	const colors: Record<string, string> = {
		pending:            'text-yellow-400',
		ready:              'text-amber-400',
		requires_approval:  'text-violet-400',
		agent_running:      'text-violet-400',
		awaiting_input:     'text-yellow-400',
		done:               'text-green-400',
		failed:             'text-red-600',
		dismissed:          'text-surface-500',
		archived:           'text-surface-600',
	};

	const animate: Record<string, string> = {
		pending: 'animate-pulse',
		agent_running: 'animate-pulse',
		awaiting_input: 'animate-pulse',
	};

	const colorClass = $derived(colors[status] ?? 'text-surface-500');
	const animateClass = $derived(animate[status] ?? '');
	const sizeClass = $derived(size === 'md' ? 'h-[11px] w-[11px]' : 'h-2 w-2');
</script>

<!-- Each status gets a unique shape so meaning isn't conveyed by color alone -->
<svg class="{sizeClass} shrink-0 {colorClass} {animateClass}" viewBox="0 0 8 8" fill="none" aria-hidden="true">
	{#if status === 'done'}
		<!-- Checkmark -->
		<path d="M1.5 4.5 L3 6 L6.5 2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
	{:else if status === 'failed'}
		<!-- X mark -->
		<path d="M2 2 L6 6 M6 2 L2 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	{:else if status === 'requires_approval'}
		<!-- Diamond -->
		<path d="M4 1 L7 4 L4 7 L1 4 Z" fill="currentColor" />
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
