<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { ProviderModels } from '$lib/api/types';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';

	interface Props {
		id?: string;
		value: string;
		providers: ProviderModels[];
		onchange: (value: string) => void;
		placeholder?: string;
		allowEmpty?: boolean;
		emptyLabel?: string;
	}

	let {
		id = '',
		value = $bindable(),
		providers,
		onchange,
		placeholder = 'Select a model...',
		allowEmpty = false,
		emptyLabel = 'Use default'
	}: Props = $props();

	let open = $state(false);
	let search = $state('');
	let highlightIndex = $state(-1);
	let triggerRef = $state<HTMLButtonElement | null>(null);
	let panelRef = $state<HTMLDivElement | null>(null);
	let searchRef = $state<HTMLInputElement | null>(null);
	let dropPos = $state({ top: 0, left: 0, width: 0 });

	// Build flat list of filtered options for keyboard nav
	let filteredProviders = $derived.by(() => {
		const q = search.toLowerCase().trim();
		if (!q) return providers;
		return providers
			.map((p) => ({
				...p,
				models: p.models.filter(
					(m) =>
						m.name.toLowerCase().includes(q) ||
						m.id.toLowerCase().includes(q)
				)
			}))
			.filter((p) => p.models.length > 0);
	});

	let flatOptions = $derived.by(() => {
		const items: { id: string; name: string; provider: string }[] = [];
		if (allowEmpty) {
			items.push({ id: '', name: emptyLabel, provider: '' });
		}
		for (const p of filteredProviders) {
			for (const m of p.models) {
				items.push({ id: m.id, name: m.name, provider: p.label });
			}
		}
		return items;
	});

	// Resolve display label for current value
	let displayLabel = $derived.by(() => {
		if (!value) return allowEmpty ? emptyLabel : placeholder;
		for (const p of providers) {
			const m = p.models.find((m) => m.id === value);
			if (m) return m.name;
		}
		return value;
	});

	function toggle() {
		open = !open;
		if (open) {
			search = '';
			highlightIndex = -1;
			if (triggerRef) {
				const r = triggerRef.getBoundingClientRect();
				dropPos = { top: r.bottom + 4, left: r.left, width: r.width };
			}
			requestAnimationFrame(() => searchRef?.focus());
		}
	}

	function select(modelId: string) {
		value = modelId;
		open = false;
		search = '';
		onchange(modelId);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!open) {
			if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
				e.preventDefault();
				toggle();
			}
			return;
		}

		switch (e.key) {
			case 'ArrowDown':
				e.preventDefault();
				highlightIndex = Math.min(highlightIndex + 1, flatOptions.length - 1);
				break;
			case 'ArrowUp':
				e.preventDefault();
				highlightIndex = Math.max(highlightIndex - 1, 0);
				break;
			case 'Enter':
				e.preventDefault();
				if (highlightIndex >= 0 && highlightIndex < flatOptions.length) {
					select(flatOptions[highlightIndex].id);
				}
				break;
			case 'Escape':
				e.preventDefault();
				open = false;
				search = '';
				break;
		}
	}

	// Close on outside click
	function handleWindowClick(e: MouseEvent) {
		const target = e.target as Node;
		if (triggerRef?.contains(target)) return;
		if (panelRef?.contains(target)) return;
		open = false;
		search = '';
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="relative">
	<!-- Trigger button -->
	<button
		bind:this={triggerRef}
		type="button"
		{id}
		onclick={toggle}
		onkeydown={handleKeydown}
		class="flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-laya-base text-surface-100 transition-colors {$glassTheme ? 'glass-input hover:border-white/25' : 'border-surface-600 bg-surface-700 hover:border-surface-500'}"
	>
		<span class="truncate {!value && !allowEmpty ? 'text-surface-500' : ''}">{displayLabel}</span>
		<svg
			class="ml-2 h-4 w-4 shrink-0 text-surface-400 transition-transform {open
				? 'rotate-180'
				: ''}"
			fill="none"
			stroke="currentColor"
			viewBox="0 0 24 24"
		>
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
		</svg>
	</button>

	<!-- Dropdown panel — portaled to body for proper frosted glass -->
	{#if open}
		<div
			use:portal
			bind:this={panelRef}
			class="fixed z-[100] rounded-md border shadow-lg {$glassTheme ? 'glass-dropdown border-white/15' : 'border-surface-600 bg-surface-800'}"
			style="top: {dropPos.top}px; left: {dropPos.left}px; width: {dropPos.width}px;"
		>
			<!-- Search input -->
			<div class="border-b {$glassTheme ? 'border-white/[0.08]' : 'border-surface-700'} p-2">
				<input
					bind:this={searchRef}
					bind:value={search}
					onkeydown={handleKeydown}
					type="text"
					placeholder="Search models..."
					class="w-full rounded border px-2 py-1.5 text-laya-base text-surface-100 placeholder:text-surface-500 focus:outline-none {$glassTheme ? 'glass-input' : 'border-surface-600 bg-surface-700 focus:border-surface-500'}"
				/>
			</div>

			<!-- Options list -->
			<div class="max-h-64 overflow-y-auto py-1">
				{#if allowEmpty}
					<button
						type="button"
						onclick={() => select('')}
						class="w-full px-3 py-1.5 text-left text-laya-base transition-colors {value === ''
							? ($glassTheme ? 'bg-white/10 text-surface-100' : 'bg-surface-600 text-surface-100')
							: ($glassTheme ? 'text-surface-400 hover:bg-white/8' : 'text-surface-400 hover:bg-surface-700')}"
					>
						{emptyLabel}
					</button>
				{/if}

				{#each filteredProviders as providerGroup}
					<div class="px-3 pb-0.5 pt-2 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">
						{providerGroup.label}
					</div>
					{#each providerGroup.models as model, i}
						{@const flatIdx = flatOptions.findIndex((o) => o.id === model.id)}
						<button
							type="button"
							onclick={() => select(model.id)}
							class="w-full truncate px-3 py-1.5 text-left text-laya-base transition-colors
								{model.id === value
								? ($glassTheme ? 'bg-white/[0.12] text-surface-100 font-medium' : 'bg-surface-600 text-surface-100 font-medium')
								: flatIdx === highlightIndex
									? ($glassTheme ? 'bg-white/[0.08] text-surface-100' : 'bg-surface-700 text-surface-100')
									: ($glassTheme ? 'text-surface-300 hover:bg-white/[0.06]' : 'text-surface-300 hover:bg-surface-700')}"
						>
							{model.name}
						</button>
					{/each}
				{/each}

				{#if filteredProviders.length === 0}
					<div class="px-3 py-3 text-center text-laya-base text-surface-500">
						{providers.length === 0 ? 'No API keys configured' : 'No models match your search'}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
