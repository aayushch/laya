<script lang="ts">
	let {
		onsubmit,
		loading = false,
		initialQuery = ''
	}: {
		onsubmit: (query: string) => void;
		loading?: boolean;
		initialQuery?: string;
	} = $props();

	let query = $state('');

	// Sync initial query when it changes (e.g., loading a saved trace)
	$effect(() => {
		if (initialQuery) query = initialQuery;
	});

	function handleSubmit(e: Event) {
		e.preventDefault();
		const trimmed = query.trim();
		if (trimmed && !loading) {
			onsubmit(trimmed);
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			handleSubmit(e);
		}
	}
</script>

<form onsubmit={handleSubmit} class="w-full max-w-2xl mx-auto">
	<div class="relative">
		<div class="absolute left-4 top-1/2 -translate-y-1/2 text-surface-400">
			{#if loading}
				<svg class="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
					<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" opacity="0.25" />
					<path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
				</svg>
			{:else}
				<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
				</svg>
			{/if}
		</div>
		<input
			type="text"
			bind:value={query}
			onkeydown={handleKeydown}
			placeholder="Search for any entity... e.g. PR-123, BUG-456, deployment issue"
			disabled={loading}
			class="w-full pl-12 pr-24 py-4 rounded-xl bg-surface-800 border border-surface-700
			       text-surface-50 placeholder-surface-500 text-lg
			       focus:outline-none focus:border-laya-orange/50 focus:ring-1 focus:ring-laya-orange/30
			       disabled:opacity-50 transition-colors"
		/>
		<button
			type="submit"
			disabled={!query.trim() || loading}
			class="absolute right-2 top-1/2 -translate-y-1/2 px-5 py-2 rounded-lg
			       bg-laya-orange text-white font-medium text-sm
			       hover:bg-laya-orange/90 disabled:opacity-40 disabled:cursor-not-allowed
			       transition-colors"
		>
			Search
		</button>
	</div>
</form>
