<script lang="ts">
	let {
		onsubmit,
		loading = false,
		initialQuery = ''
	}: {
		onsubmit: (query: string, fuzzy: boolean, opts?: {
			enableSemantic?: boolean;
			enableText?: boolean;
			enableLlmFilter?: boolean;
		}) => void;
		loading?: boolean;
		initialQuery?: string;
	} = $props();

	let query = $state('');

	// Advanced search settings — defaults match the backend defaults
	let showAdvanced = $state(false);
	let enableSemantic = $state(true);
	let enableText = $state(true);
	let enableFuzzy = $state(false);
	let enableLlmFilter = $state(true);

	// Whether any advanced setting is non-default
	const hasCustomSettings = $derived(
		!enableSemantic || !enableText || enableFuzzy || !enableLlmFilter
	);

	// Tooltip state
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}

	function hideTooltip() { tooltip = null; }

	// Sync initial query when it changes (e.g., loading a saved trace)
	$effect(() => {
		if (initialQuery) query = initialQuery;
	});

	function handleSubmit(e: Event) {
		e.preventDefault();
		const trimmed = query.trim();
		if (trimmed && !loading) {
			const opts = hasCustomSettings ? {
				enableSemantic,
				enableText,
				enableLlmFilter,
			} : undefined;
			onsubmit(trimmed, enableFuzzy, opts);
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			handleSubmit(e);
		}
	}

	function resetAdvanced() {
		enableSemantic = true;
		enableText = true;
		enableFuzzy = false;
		enableLlmFilter = true;
	}
</script>

<!-- Fixed-position tooltip -->
{#if tooltip}
	<div
		class="fixed z-50 px-2.5 py-1 rounded-md bg-surface-700 text-surface-100 text-xs font-medium shadow-lg pointer-events-none -translate-x-1/2 -translate-y-full"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

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
			placeholder="Trace an entity across your tools — tickets, PRs, threads, deploys..."
			disabled={loading}
			class="w-full pl-12 pr-28 py-4 rounded-xl bg-surface-800 border border-surface-700
			       text-surface-50 placeholder-surface-500 text-sm
			       focus:outline-none focus:border-laya-orange/50 focus:ring-1 focus:ring-laya-orange/30
			       disabled:opacity-50 transition-colors"
		/>
		<div class="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2">
			<!-- Search Settings toggle -->
			<button
				type="button"
				onclick={() => (showAdvanced = !showAdvanced)}
				onmouseenter={(e) => showTooltip(e, 'Search settings')}
				onmouseleave={hideTooltip}
				aria-label="Search settings"
				class="p-1.5 rounded-lg transition-colors
				       {showAdvanced || hasCustomSettings
					? 'bg-laya-orange/20 text-laya-orange border border-laya-orange/40'
					: 'bg-surface-700/60 text-surface-400 border border-transparent hover:text-surface-300 hover:bg-surface-700'}"
			>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
				</svg>
			</button>
			<button
				type="submit"
				disabled={!query.trim() || loading}
				class="px-5 py-2 rounded-lg
				       bg-laya-orange text-white font-medium text-sm
				       hover:bg-laya-orange/90 disabled:opacity-40 disabled:cursor-not-allowed
				       transition-colors"
			>
				Search
			</button>
		</div>
	</div>

	<!-- Search Settings Panel -->
	{#if showAdvanced}
		<div class="mt-2 rounded-xl border border-surface-700 bg-surface-800/80 p-4">
			<div class="flex items-center justify-between mb-3">
				<h4 class="text-xs font-semibold uppercase tracking-wider text-surface-400">Search Settings</h4>
				{#if hasCustomSettings}
					<button
						type="button"
						onclick={resetAdvanced}
						class="text-[10px] text-surface-500 hover:text-surface-300 transition-colors"
					>
						Reset to defaults
					</button>
				{/if}
			</div>

			<div class="flex flex-col gap-3">
				<!-- Semantic Search -->
				<label class="flex items-center gap-3 cursor-pointer group">
					<button
						type="button"
						role="switch"
						aria-checked={enableSemantic}
						aria-label="Toggle semantic search"
						onclick={() => (enableSemantic = !enableSemantic)}
						class="relative w-8 h-[18px] rounded-full transition-colors {enableSemantic ? 'bg-laya-orange/60' : 'bg-surface-700'}"
					>
						<span class="absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full transition-all {enableSemantic ? 'translate-x-[14px] bg-white' : 'bg-surface-400'}"></span>
					</button>
					<div class="flex-1">
						<span class="text-xs font-medium text-surface-200 group-hover:text-surface-50 transition-colors">Semantic search</span>
						<p class="text-[10px] text-surface-500 leading-tight">Vector similarity via embeddings — finds conceptually related items</p>
					</div>
				</label>

				<!-- Text Search (phrase match) -->
				<label class="flex items-center gap-3 cursor-pointer group">
					<button
						type="button"
						role="switch"
						aria-checked={enableText}
						aria-label="Toggle text search"
						onclick={() => (enableText = !enableText)}
						class="relative w-8 h-[18px] rounded-full transition-colors {enableText ? 'bg-laya-orange/60' : 'bg-surface-700'}"
					>
						<span class="absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full transition-all {enableText ? 'translate-x-[14px] bg-white' : 'bg-surface-400'}"></span>
					</button>
					<div class="flex-1">
						<span class="text-xs font-medium text-surface-200 group-hover:text-surface-50 transition-colors">Text search</span>
						<p class="text-[10px] text-surface-500 leading-tight">Exact phrase match on titles, descriptions, and event content</p>
					</div>
				</label>

				<!-- Fuzzy Search (keyword split) -->
				<label class="flex items-center gap-3 cursor-pointer group">
					<button
						type="button"
						role="switch"
						aria-checked={enableFuzzy}
						aria-label="Toggle fuzzy search"
						onclick={() => (enableFuzzy = !enableFuzzy)}
						class="relative w-8 h-[18px] rounded-full transition-colors {enableFuzzy ? 'bg-laya-orange/60' : 'bg-surface-700'}"
					>
						<span class="absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full transition-all {enableFuzzy ? 'translate-x-[14px] bg-white' : 'bg-surface-400'}"></span>
					</button>
					<div class="flex-1">
						<span class="text-xs font-medium text-surface-200 group-hover:text-surface-50 transition-colors">Fuzzy search</span>
						<p class="text-[10px] text-surface-500 leading-tight">Broad keyword matching — each word matched independently (noisier results)</p>
					</div>
				</label>

				<!-- LLM Filter -->
				<label class="flex items-center gap-3 cursor-pointer group">
					<button
						type="button"
						role="switch"
						aria-checked={enableLlmFilter}
						aria-label="Toggle AI relevance filter"
						onclick={() => (enableLlmFilter = !enableLlmFilter)}
						class="relative w-8 h-[18px] rounded-full transition-colors {enableLlmFilter ? 'bg-laya-orange/60' : 'bg-surface-700'}"
					>
						<span class="absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full transition-all {enableLlmFilter ? 'translate-x-[14px] bg-white' : 'bg-surface-400'}"></span>
					</button>
					<div class="flex-1">
						<span class="text-xs font-medium text-surface-200 group-hover:text-surface-50 transition-colors">AI relevance filter</span>
						<p class="text-[10px] text-surface-500 leading-tight">Uses a model to remove false positives — adds latency but improves precision</p>
					</div>
				</label>
			</div>

		</div>
	{/if}
</form>
