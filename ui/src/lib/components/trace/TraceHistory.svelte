<script lang="ts">
	import type { TraceListItem } from '$lib/api/types';
	import { slide } from 'svelte/transition';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';

	let {
		traces,
		onselect,
		ondelete,
		onrerun
	}: {
		traces: TraceListItem[];
		onselect: (traceId: string) => void;
		ondelete?: (traceId: string) => void;
		onrerun?: (traceId: string) => void;
	} = $props();

	function handleDelete(e: MouseEvent, traceId: string) {
		e.stopPropagation();
		tooltip = null; // Clear tooltip before element is removed
		ondelete?.(traceId);
	}

	// Tooltip state
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}

	function hideTooltip() { tooltip = null; }

</script>

<!-- Fixed-position tooltip -->
{#if tooltip}
	<div
		use:portal
		class="fixed z-[100] px-2.5 py-1 rounded-md border border-transparent glass-tooltip text-laya-secondary font-medium shadow-lg pointer-events-none -translate-x-1/2 -translate-y-full"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

{#if traces.length === 0}
	<div class="text-center py-12 text-surface-500">
		<svg class="w-12 h-12 mx-auto mb-3 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
			<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
		</svg>
		<p class="text-laya-base">No searches yet. Search for an entity to get started.</p>
	</div>
{:else}
	<div class="space-y-2">
		{#each traces as trace (trace.trace_id)}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				out:slide={{ duration: $reducedMotion ? 0 : 250 }}
				class="w-full text-left rounded-lg p-4 transition-colors group cursor-pointer
				       {$glassTheme
				         ? 'glass-card border border-transparent hover:border-white/[0.1]'
				         : 'border border-surface-700/60 bg-surface-800/60 hover:border-surface-600 hover:bg-surface-800'}"
				onclick={() => onselect(trace.trace_id)}
				onkeydown={(e) => { if (e.key === 'Enter') onselect(trace.trace_id); }}
				role="button"
				tabindex="0"
			>
				<!-- Row 1: Title + action buttons -->
				<div class="flex items-center justify-between gap-3">
					<div class="flex items-center gap-2 min-w-0">
						<h3 class="text-laya-base font-medium text-surface-100 truncate group-hover:text-laya-orange transition-colors">
							"{trace.query}"
						</h3>
						{#if trace.fuzzy_search}
							<span class="shrink-0 px-1.5 py-0.5 rounded text-laya-micro font-medium bg-laya-orange/15 text-laya-orange border border-laya-orange/30">
								Fuzzy
							</span>
						{/if}
					</div>
					<div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
						{#if onrerun}
							<button
								onclick={(e) => { e.stopPropagation(); onrerun?.(trace.trace_id); }}
								onmouseenter={(e) => showTooltip(e, 'Re-run')}
								onmouseleave={hideTooltip}
								aria-label="Re-run"
								class="p-1.5 rounded text-surface-400 hover:text-surface-200 {$glassTheme ? 'glass-hover' : 'hover:bg-surface-700'} transition-colors"
							>
								<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
									<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
								</svg>
							</button>
						{/if}
						{#if ondelete}
							<button
								onclick={(e) => handleDelete(e, trace.trace_id)}
								onmouseenter={(e) => showTooltip(e, 'Delete')}
								onmouseleave={hideTooltip}
								aria-label="Delete"
								class="p-1.5 rounded text-surface-400 hover:text-red-400 {$glassTheme ? 'glass-hover' : 'hover:bg-surface-700'} transition-colors"
							>
								<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
									<path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
								</svg>
							</button>
						{/if}
					</div>
				</div>
				<!-- Row 2: Metadata + search setting badges -->
				<div class="flex items-center gap-2 mt-1.5">
					{#each trace.platforms as platform}
						<PlatformBadge {platform} />
					{/each}
					<span class="text-laya-secondary text-surface-500">
						{trace.total_cards} cards
					</span>
					<span class="text-laya-secondary text-surface-600">
						{new Date(trace.created_at).toLocaleDateString(undefined, {
							month: 'short',
							day: 'numeric',
							hour: '2-digit',
							minute: '2-digit'
						})}
					</span>
					{#if trace.enable_semantic || trace.enable_text || trace.enable_llm_filter || trace.fuzzy_search}
						<div class="flex items-center gap-1.5 ml-auto">
							{#if trace.enable_semantic}
								<span class="px-1.5 py-0.5 rounded bg-laya-orange/10 text-laya-orange text-laya-micro font-medium">Semantic</span>
							{/if}
							{#if trace.enable_text}
								<span class="px-1.5 py-0.5 rounded bg-laya-gold/10 text-laya-gold text-laya-micro font-medium">Text</span>
							{/if}
							{#if trace.enable_llm_filter}
								<span class="px-1.5 py-0.5 rounded bg-laya-peach/10 text-laya-peach text-laya-micro font-medium">AI Filter</span>
							{/if}
							{#if trace.fuzzy_search}
								<span class="px-1.5 py-0.5 rounded bg-laya-coral/10 text-laya-coral text-laya-micro font-medium">Fuzzy</span>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{/if}
