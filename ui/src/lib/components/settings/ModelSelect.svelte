<script lang="ts">
	import type { ProviderModels } from '$lib/api/types';

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
	let dropdownRef = $state<HTMLDivElement | null>(null);
	let searchRef = $state<HTMLInputElement | null>(null);

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
		// Model not in list — show raw value (e.g. previously configured model)
		return value;
	});

	function toggle() {
		open = !open;
		if (open) {
			search = '';
			highlightIndex = -1;
			// Focus search input after opening
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
		if (dropdownRef && !dropdownRef.contains(e.target as Node)) {
			open = false;
			search = '';
		}
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="relative" bind:this={dropdownRef}>
	<!-- Trigger button -->
	<button
		type="button"
		{id}
		onclick={toggle}
		onkeydown={handleKeydown}
		class="flex w-full items-center justify-between rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-left text-sm text-surface-100 transition-colors hover:border-surface-500"
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

	<!-- Dropdown panel -->
	{#if open}
		<div
			class="absolute z-50 mt-1 w-full rounded-md border border-surface-600 bg-surface-800 shadow-lg"
		>
			<!-- Search input -->
			<div class="border-b border-surface-700 p-2">
				<input
					bind:this={searchRef}
					bind:value={search}
					onkeydown={handleKeydown}
					type="text"
					placeholder="Search models..."
					class="w-full rounded border border-surface-600 bg-surface-700 px-2 py-1.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-surface-500 focus:outline-none"
				/>
			</div>

			<!-- Options list -->
			<div class="max-h-64 overflow-y-auto py-1">
				{#if allowEmpty}
					<button
						type="button"
						onclick={() => select('')}
						class="w-full px-3 py-1.5 text-left text-sm transition-colors {value === ''
							? 'bg-surface-600 text-surface-100'
							: 'text-surface-400 hover:bg-surface-700'}"
					>
						{emptyLabel}
					</button>
				{/if}

				{#each filteredProviders as providerGroup}
					<div class="px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wider text-surface-500">
						{providerGroup.label}
					</div>
					{#each providerGroup.models as model, i}
						{@const flatIdx = flatOptions.findIndex((o) => o.id === model.id)}
						<button
							type="button"
							onclick={() => select(model.id)}
							class="w-full truncate px-3 py-1.5 text-left text-sm transition-colors
								{model.id === value
								? 'bg-surface-600 text-surface-100 font-medium'
								: flatIdx === highlightIndex
									? 'bg-surface-700 text-surface-100'
									: 'text-surface-300 hover:bg-surface-700'}"
						>
							{model.name}
						</button>
					{/each}
				{/each}

				{#if filteredProviders.length === 0}
					<div class="px-3 py-3 text-center text-sm text-surface-500">
						{providers.length === 0 ? 'No API keys configured' : 'No models match your search'}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
