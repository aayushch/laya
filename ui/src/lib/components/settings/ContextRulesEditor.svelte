<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { flip } from 'svelte/animate';
	import { cubicInOut } from 'svelte/easing';
	import type { ContextRule } from '$lib/api/types';

	// Out-transition for a deleted rule: fades the card while collapsing its
	// height (incl. padding/margin) so neighbouring rules visibly slide up to
	// fill the gap. Paired with animate:flip on the same element. Without this
	// the deleted rule vanishes instantly and users can't tell which one went.
	function collapseFade(node: HTMLElement, { duration = 260 } = {}) {
		const style = getComputedStyle(node);
		const height = parseFloat(style.height);
		const paddingTop = parseFloat(style.paddingTop);
		const paddingBottom = parseFloat(style.paddingBottom);
		const marginTop = parseFloat(style.marginTop);
		const marginBottom = parseFloat(style.marginBottom);
		return {
			duration,
			easing: cubicInOut,
			css: (t: number) =>
				`opacity: ${t};` +
				`height: ${t * height}px;` +
				`padding-top: ${t * paddingTop}px;` +
				`padding-bottom: ${t * paddingBottom}px;` +
				`margin-top: ${t * marginTop}px;` +
				`margin-bottom: ${t * marginBottom}px;` +
				`overflow: hidden;`
		};
	}

	// Context rules are learned from link/unlink actions (source 'learned') and
	// can grow large, so this list is server-paginated. Users can also add
	// manual rules and edit/delete/disable any rule.
	let rules = $state<ContextRule[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let saving = $state(false);

	let editingId = $state<number | null>(null);
	let showAddForm = $state(false);
	let formText = $state('');

	let offset = $state(0);
	const limit = 15;

	const page = $derived(Math.floor(offset / limit) + 1);
	const totalPages = $derived(Math.ceil(total / limit) || 1);
	const formValid = $derived(formText.trim() !== '');

	// `silent` refetches without flipping `loading` — used after a delete so the
	// list isn't unmounted mid out-transition (which would kill the animation).
	async function load(silent = false) {
		if (!silent) loading = true;
		try {
			const resp = await engineApi.getContextRules({ limit, offset });
			rules = resp.rules;
			total = resp.total;
		} catch {
			error = 'Failed to load context rules';
		} finally {
			if (!silent) loading = false;
		}
	}

	onMount(load);

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

	function resetForm() {
		formText = '';
		showAddForm = false;
		editingId = null;
	}

	async function addRule() {
		saving = true;
		error = null;
		try {
			await engineApi.createContextRule({ rule_text: formText.trim() });
			resetForm();
			offset = 0; // newest first — jump to the top so the new rule is visible
			await load();
		} catch {
			error = 'Failed to create rule';
		} finally {
			saving = false;
		}
	}

	function startEdit(rule: ContextRule) {
		editingId = rule.id;
		formText = rule.rule_text;
		showAddForm = false;
	}

	async function saveEdit() {
		if (editingId === null) return;
		saving = true;
		error = null;
		try {
			await engineApi.updateContextRule(editingId, { rule_text: formText.trim() });
			resetForm();
			await load();
		} catch {
			error = 'Failed to update rule';
		} finally {
			saving = false;
		}
	}

	async function toggleRule(rule: ContextRule) {
		try {
			await engineApi.updateContextRule(rule.id, { active: !rule.active });
			const idx = rules.findIndex((r) => r.id === rule.id);
			if (idx !== -1) rules[idx] = { ...rules[idx], active: !rule.active };
		} catch {
			error = 'Failed to toggle rule';
		}
	}

	async function removeRule(rule: ContextRule) {
		try {
			await engineApi.deleteContextRule(rule.id);
			// Optimistically drop the row so the out-transition plays; reassigning
			// the whole array via load() would flip `loading` and unmount the list.
			rules = rules.filter((r) => r.id !== rule.id);
			total = Math.max(0, total - 1);
			// If that emptied a non-first page, step back one.
			if (rules.length === 0 && offset >= limit) offset -= limit;
			// Silently refetch to pull the next page's first row up into this page
			// (keeps it full) without a loading flash that would cut the animation.
			await load(true);
		} catch {
			error = 'Failed to delete rule';
		}
	}
</script>

<div class="space-y-4">
	<div>
		<h3 class="text-laya-heading font-semibold text-surface-50">Context Rules</h3>
		<p class="mt-1 text-laya-base text-surface-400">
			Context rules guide how Laya decides whether two notifications belong to the same context
			group. Most are learned automatically from your link/unlink actions; you can edit, disable,
			or add your own. They're injected into the AI's grouping instructions.
		</p>
	</div>

	{#if error}
		<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-laya-base text-red-300">{error}</div>
	{/if}

	{#if loading}
		<div class="text-surface-400 text-laya-base">Loading context rules...</div>
	{:else}
		<!-- Top pagination bar: surfaces the rule count + page controls above the
		     table so they're visible without scrolling to the bottom of a long list. -->
		<div class="flex items-center justify-between text-laya-secondary text-surface-400">
			<span>{total} rule{total !== 1 ? 's' : ''}</span>
			{#if total > limit}
				<div class="flex items-center gap-2">
					<button onclick={prevPage} disabled={offset === 0} class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30">Prev</button>
					<span>{page} / {totalPages}</span>
					<button onclick={nextPage} disabled={offset + limit >= total} class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30">Next</button>
				</div>
			{/if}
		</div>

		<div class="space-y-2">
			{#each rules as rule (rule.id)}
				<div animate:flip={{ duration: 260 }} out:collapseFade>
				{#if editingId === rule.id}
					<!-- Inline edit form -->
					<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} border-laya-orange/30 p-3 space-y-2">
						<input
							bind:value={formText}
							placeholder="Rule text"
							class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500"
						/>
						<div class="flex gap-2">
							<button
								class="rounded-lg {$glassTheme ? 'bg-white/10 hover:bg-white/15' : 'bg-surface-600 hover:bg-surface-500'} px-4 py-1.5 text-laya-base font-medium disabled:opacity-50"
								onclick={saveEdit}
								disabled={!formValid || saving}
							>
								{saving ? 'Saving...' : 'Save'}
							</button>
							<button class="rounded-lg px-4 py-1.5 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetForm}>
								Cancel
							</button>
						</div>
					</div>
				{:else}
					<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} px-3 py-2">
						<div class="flex items-center gap-3">
							<button
								class="relative h-5 w-9 shrink-0 rounded-full transition-colors {rule.active ? 'bg-green-600' : 'bg-surface-600'}"
								onclick={() => toggleRule(rule)}
								aria-label="Toggle rule"
							>
								<span class="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform {rule.active ? 'left-[1.125rem]' : 'left-0.5'}"></span>
							</button>
							<span class="rounded px-1.5 py-0.5 text-laya-micro font-semibold uppercase shrink-0
								{rule.source === 'learned' ? 'bg-blue-900/50 text-blue-400' : 'bg-surface-700 text-surface-400'}">
								{rule.source}
							</span>
							{#if rule.space_id}
								<span class="rounded px-1.5 py-0.5 text-laya-micro text-surface-500 shrink-0" title="Space">{rule.space_id}</span>
							{/if}
							<p class="flex-1 min-w-0 text-laya-base {rule.active ? 'text-surface-200' : 'text-surface-500'}">{rule.rule_text}</p>
							<div class="flex items-center gap-1 shrink-0">
								<button class="rounded p-1 text-surface-500 transition-colors hover:text-surface-200" onclick={() => startEdit(rule)} aria-label="Edit rule" title="Edit">
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
								</button>
								<button class="rounded p-1 text-surface-500 transition-colors hover:text-red-400" onclick={() => removeRule(rule)} aria-label="Remove rule" title="Remove">
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
								</button>
							</div>
						</div>
					</div>
				{/if}
				</div>
			{/each}
			{#if rules.length === 0}
				<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-6 text-center text-surface-500">
					No context rules yet. They're learned as you link/unlink cards, or add one below.
				</div>
			{/if}
		</div>

		<!-- Add form -->
		{#if showAddForm}
			<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-3 space-y-2">
				<input
					bind:value={formText}
					placeholder='e.g., "Group a deployment alert with the incident thread that references the same service"'
					class="w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500"
				/>
				<div class="flex gap-2">
					<button
						class="rounded-lg {$glassTheme ? 'bg-white/10 hover:bg-white/15' : 'bg-surface-600 hover:bg-surface-500'} px-4 py-1.5 text-laya-base font-medium disabled:opacity-50"
						onclick={addRule}
						disabled={!formValid || saving}
					>
						{saving ? 'Saving...' : 'Add'}
					</button>
					<button class="rounded-lg px-4 py-1.5 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetForm}>
						Cancel
					</button>
				</div>
			</div>
		{:else if editingId === null}
			<button
				class="rounded-lg border border-dashed border-surface-600 px-4 py-2 text-laya-base text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
				onclick={() => (showAddForm = true)}
			>
				+ Add Context Rule
			</button>
		{/if}
	{/if}
</div>
