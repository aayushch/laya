<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import Dropdown from '$lib/components/Dropdown.svelte';
	import type { ProcessingRule, ProcessingRuleFiringEntry } from '$lib/api/types';

	// Rules are already loaded by the parent (ProcessingRulesEditor); passed in
	// so the rule filter dropdown needs no extra fetch.
	let { rules }: { rules: ProcessingRule[] } = $props();

	let entries: ProcessingRuleFiringEntry[] = $state([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	// Filter values are strings (Dropdown binds strings); '' means "all".
	let filterRuleId = $state('');
	let filterOutcome = $state<'' | 'success' | 'error' | 'skipped'>('');
	let searchQuery = $state('');
	let limit = 25;
	let offset = $state(0);

	const ruleOptions = $derived([
		{ value: '', label: 'All rules' },
		...rules.map((r) => ({ value: String(r.id), label: r.name }))
	]);

	async function load() {
		loading = true;
		error = '';
		try {
			const params: Parameters<typeof engineApi.getProcessingRuleFirings>[0] = { limit, offset };
			if (filterRuleId !== '') params.rule_id = Number(filterRuleId);
			if (filterOutcome !== '') params.outcome = filterOutcome;
			if (searchQuery.trim()) params.search = searchQuery.trim();
			const resp = await engineApi.getProcessingRuleFirings(params);
			entries = resp.entries;
			total = resp.total;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load firing log';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function applyFilter() {
		offset = 0;
		load();
	}

	function clearFilters() {
		filterRuleId = '';
		filterOutcome = '';
		searchQuery = '';
		applyFilter();
	}

	function prevPage() {
		if (offset >= limit) {
			offset -= limit;
			load();
		}
	}

	function nextPage() {
		if (offset + limit < total) {
			offset += limit;
			load();
		}
	}

	const page = $derived(Math.floor(offset / limit) + 1);
	const totalPages = $derived(Math.ceil(total / limit) || 1);
	const hasFilters = $derived(filterRuleId !== '' || filterOutcome !== '' || searchQuery.trim() !== '');

	function formatTime(ts: string): string {
		const utc = ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z';
		return new Date(utc).toLocaleString([], {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit'
		});
	}

	function humanizeAction(type: string): string {
		return type.replace(/_/g, ' ');
	}

	// ── Portal tooltip (mirrors AuditLogViewer) ──
	let fixedTooltip = $state<{ text: string; top: number; left?: number; right?: number; maxWidth: number; color?: string } | null>(null);

	function showTooltip(el: HTMLElement, text: string, opts?: { maxWidth?: number; color?: string }) {
		const rect = el.getBoundingClientRect();
		const useRight = rect.left > window.innerWidth / 2;
		fixedTooltip = {
			text, top: rect.bottom + 4, maxWidth: opts?.maxWidth ?? 320, color: opts?.color,
			...(useRight ? { right: window.innerWidth - rect.right } : { left: rect.left })
		};
	}

	function hideTooltip() { fixedTooltip = null; }

	// First failing result's error message (the firing's `error` column only
	// carries the last action's error, so fall back to scanning results).
	function errorText(entry: ProcessingRuleFiringEntry): string {
		if (entry.error) return entry.error;
		const failed = entry.results.find((r) => r.success === false);
		return failed?.error ?? 'Action failed';
	}
</script>

<div class="space-y-4">
	<!-- Filters -->
	<div class="flex flex-wrap items-end gap-3">
		<div class="w-48">
			<span class="mb-1 block text-laya-secondary text-surface-400">Rule</span>
			<Dropdown
				bind:value={filterRuleId}
				options={ruleOptions}
				onchange={(v) => { filterRuleId = v; }}
			/>
		</div>
		<div class="w-32">
			<span class="mb-1 block text-laya-secondary text-surface-400">Outcome</span>
			<Dropdown
				bind:value={filterOutcome}
				options={[
					{ value: '', label: 'All' },
					{ value: 'success', label: 'Success' },
					{ value: 'error', label: 'Error' },
					{ value: 'skipped', label: 'Skipped' }
				]}
				onchange={(v) => { filterOutcome = v as typeof filterOutcome; }}
			/>
		</div>
		<button
			onclick={applyFilter}
			class="h-[38px] rounded-lg px-4 text-laya-base font-medium text-surface-200 transition-colors {$glassTheme ? 'bg-white/[0.08] hover:bg-white/[0.14]' : 'bg-surface-700 hover:bg-surface-600'}"
		>
			Apply
		</button>
		{#if hasFilters}
			<button
				onclick={clearFilters}
				class="h-[38px] rounded-lg px-3 text-laya-base text-surface-400 transition-colors hover:text-surface-200"
			>
				Clear
			</button>
		{/if}

		<!-- Search box — right-aligned -->
		<div class="relative ml-auto w-64">
			<svg class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
			</svg>
			<input
				type="text"
				bind:value={searchQuery}
				placeholder="Search firings…"
				onkeydown={(e) => { if (e.key === 'Enter') applyFilter(); }}
				class="h-[38px] w-full rounded-md border pl-8 pr-3 text-laya-secondary text-surface-200 placeholder-surface-500 focus:outline-none
					{$glassTheme ? 'glass-input focus:border-laya-orange/50' : 'border-surface-600 bg-surface-900 focus:border-laya-orange/50'}"
			/>
		</div>
	</div>

	{#if loading}
		<div class="flex justify-center py-8">
			<div class="h-5 w-5 animate-spin rounded-full border-2 border-blue-400 border-t-transparent"></div>
		</div>
	{:else if error}
		<div class="rounded-lg border border-red-800 bg-red-900/20 p-3 text-laya-base text-red-300">{error}</div>
	{:else if entries.length === 0}
		<p class="py-8 text-center text-laya-base text-surface-500">No rule firings found.</p>
	{:else}
		<!-- Pagination -->
		<div class="flex items-center justify-between text-laya-secondary text-surface-400">
			<span>{total} firing{total !== 1 ? 's' : ''}</span>
			<div class="flex items-center gap-2">
				<button
					onclick={prevPage}
					disabled={offset === 0}
					class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
				>
					Prev
				</button>
				<span>{page} / {totalPages}</span>
				<button
					onclick={nextPage}
					disabled={offset + limit >= total}
					class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
				>
					Next
				</button>
			</div>
		</div>

		<div class="overflow-x-auto {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
			<table class="w-full text-left text-laya-secondary">
				<thead class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-800'} text-surface-400">
					<tr>
						<th class="px-3 py-2">Time</th>
						<th class="px-3 py-2">Rule</th>
						<th class="px-3 py-2">Card</th>
						<th class="px-3 py-2">Actions</th>
						<th class="px-3 py-2">Outcome</th>
					</tr>
				</thead>
				<tbody class="divide-y {$glassTheme ? 'divide-white/[0.04]' : 'divide-surface-700/50'}">
					{#each entries as entry (entry.id)}
						<tr class="transition-colors hover:bg-surface-800/50 {entry.outcome === 'error' ? 'bg-red-900/10' : ''}">
							<td class="whitespace-nowrap px-3 py-2 text-surface-300">{formatTime(entry.fired_at)}</td>
							<td class="px-3 py-2 font-medium text-surface-200">{entry.rule_name ?? `#${entry.rule_id}`}</td>
							<td
								class="max-w-[260px] px-3 py-2 text-surface-300"
								onmouseenter={(e) => { const el = e.currentTarget; if (el.scrollWidth > el.clientWidth) showTooltip(el, entry.card_header ?? entry.card_id); }}
								onmouseleave={hideTooltip}
							>
								<span class="block truncate">{entry.card_header ?? entry.card_id}</span>
							</td>
							<td class="px-3 py-2 text-surface-400">
								<span class="block truncate">{entry.action_types.map(humanizeAction).join(', ') || '-'}</span>
							</td>
							<td class="px-3 py-2">
								{#if entry.outcome === 'success'}
									<span class="rounded-full bg-green-900/30 px-2 py-0.5 text-green-400">OK</span>
								{:else if entry.outcome === 'skipped'}
									<span
										class="rounded-full bg-laya-gold/25 px-2 py-0.5 text-laya-amber {entry.skip_reason ? 'cursor-help' : ''}"
										role="note"
										onmouseenter={(e) => entry.skip_reason && showTooltip(e.currentTarget, entry.skip_reason, { maxWidth: 360 })}
										onmouseleave={hideTooltip}
									>SKIP</span>
								{:else}
									<span
										class="cursor-help rounded-full bg-red-900/30 px-2 py-0.5 text-red-400"
										role="note"
										onmouseenter={(e) => showTooltip(e.currentTarget, errorText(entry), { maxWidth: 400, color: 'text-red-400' })}
										onmouseleave={hideTooltip}
									>ERR</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] max-w-sm whitespace-normal break-words rounded-md border border-transparent glass-tooltip px-2.5 py-1.5 text-laya-secondary font-normal {fixedTooltip.color ?? 'text-surface-200'}"
		style="top: {fixedTooltip.top}px; {fixedTooltip.right != null ? `right: ${fixedTooltip.right}px` : `left: ${fixedTooltip.left}px`}; max-width: {fixedTooltip.maxWidth}px;"
	>
		{fixedTooltip.text}
	</span>
{/if}
