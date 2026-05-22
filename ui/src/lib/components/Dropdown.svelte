<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { portal } from '$lib/actions/portal';
	import { glassTheme } from '$lib/stores/glassTheme';

	interface Option {
		value: string;
		label: string;
		group?: string;
		description?: string;
		disabled?: boolean;
	}

	interface Props {
		id?: string;
		value: string;
		options: Option[];
		onchange: (value: string) => void;
		placeholder?: string;
		/** "select" = bordered input-style trigger; "link" = inline text link trigger */
		variant?: 'select' | 'link';
		/** Match input field height — use the same h-[38px] as inputClass/selectClass */
		size?: 'sm' | 'md';
		/** Use smaller text-laya-secondary font size to match settings forms */
		compact?: boolean;
		disabled?: boolean;
		class?: string;
	}

	let {
		id = '',
		value = $bindable(),
		options,
		onchange,
		placeholder = 'Select…',
		variant = 'select',
		size = 'md',
		compact = false,
		disabled = false,
		class: extraClass = ''
	}: Props = $props();

	let open = $state(false);
	let highlightIndex = $state(-1);
	let triggerRef = $state<HTMLElement | null>(null);
	let panelRef = $state<HTMLDivElement | null>(null);
	let dropPos = $state({ top: 0, left: 0, width: 0, openUp: false });

	let displayLabel = $derived(
		options.find((o) => o.value === value)?.label ?? placeholder
	);

	let groups = $derived.by(() => {
		const map = new Map<string, Option[]>();
		for (const opt of options) {
			const g = opt.group ?? '';
			if (!map.has(g)) map.set(g, []);
			map.get(g)!.push(opt);
		}
		return map;
	});

	let hasGroups = $derived([...groups.keys()].some((k) => k !== ''));

	function positionPanel() {
		if (!triggerRef) return;
		const r = triggerRef.getBoundingClientRect();
		const spaceBelow = window.innerHeight - r.bottom;
		const panelMaxH = 256;
		const openUp = spaceBelow < panelMaxH && r.top > spaceBelow;
		dropPos = {
			top: openUp ? r.top - 4 : r.bottom + 4,
			left: r.left,
			width: Math.max(r.width, 160),
			openUp
		};
	}

	function toggle() {
		if (disabled) return;
		if (open) {
			open = false;
			return;
		}
		highlightIndex = options.findIndex((o) => o.value === value);
		positionPanel();
		open = true;
		requestAnimationFrame(() => {
			scrollHighlightedIntoView();
		});
	}

	function select(val: string) {
		value = val;
		open = false;
		onchange(val);
		triggerRef?.focus();
	}

	function scrollHighlightedIntoView() {
		if (highlightIndex < 0 || !panelRef) return;
		const btns = panelRef.querySelectorAll('[data-dropdown-option]');
		btns[highlightIndex]?.scrollIntoView({ block: 'nearest' });
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
			case 'ArrowDown': {
				e.preventDefault();
				let next = highlightIndex;
				do {
					next = Math.min(next + 1, options.length - 1);
				} while (options[next]?.disabled && next < options.length - 1);
				if (!options[next]?.disabled) highlightIndex = next;
				scrollHighlightedIntoView();
				break;
			}
			case 'ArrowUp': {
				e.preventDefault();
				let next = highlightIndex;
				do {
					next = Math.max(next - 1, 0);
				} while (options[next]?.disabled && next > 0);
				if (!options[next]?.disabled) highlightIndex = next;
				scrollHighlightedIntoView();
				break;
			}
			case 'Enter':
				e.preventDefault();
				if (highlightIndex >= 0 && highlightIndex < options.length && !options[highlightIndex].disabled) {
					select(options[highlightIndex].value);
				}
				break;
			case 'Escape':
			case 'Tab':
				e.preventDefault();
				open = false;
				triggerRef?.focus();
				break;
		}
	}

	function handleWindowClick(e: MouseEvent) {
		if (!open) return;
		const target = e.target as Node;
		if (triggerRef?.contains(target)) return;
		if (panelRef?.contains(target)) return;
		open = false;
	}

	const sizeH = $derived(size === 'sm' ? 'h-[32px]' : 'h-[38px]');
	const textClass = $derived(compact ? 'text-laya-secondary' : 'text-sm');
</script>

<svelte:window onclick={handleWindowClick} />

<div class="relative {extraClass}">
	{#if variant === 'link'}
		<!-- Link trigger — inline text that opens dropdown -->
		<button
			bind:this={triggerRef}
			type="button"
			{id}
			{disabled}
			onclick={toggle}
			onkeydown={handleKeydown}
			class="inline-flex items-center gap-1 text-sm transition-colors
				{disabled
					? 'text-surface-600 cursor-not-allowed'
					: 'text-laya-orange hover:text-laya-peach cursor-pointer'}"
			aria-haspopup="listbox"
			aria-expanded={open}
		>
			<span class="underline underline-offset-2 decoration-current/40">{displayLabel}</span>
			<svg
				class="h-3 w-3 shrink-0 transition-transform {open ? 'rotate-180' : ''}"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		</button>
	{:else}
		<!-- Select trigger — bordered input-like element -->
		<button
			bind:this={triggerRef}
			data-dropdown-trigger
			type="button"
			{id}
			{disabled}
			onclick={toggle}
			onkeydown={handleKeydown}
			class="flex w-full items-center justify-between rounded-md border px-3 text-left {textClass} transition-colors
				{sizeH}
				{disabled
					? 'cursor-not-allowed opacity-50'
					: ''}
				{$glassTheme
					? 'glass-input text-surface-200 hover:border-white/25'
					: 'border-surface-600 bg-surface-900 text-surface-200 hover:border-surface-500'}"
			aria-haspopup="listbox"
			aria-expanded={open}
		>
			<span class="truncate {value ? '' : 'text-surface-500'}">{displayLabel}</span>
			<svg
				class="ml-2 h-3.5 w-3.5 shrink-0 text-surface-400 transition-transform {open ? 'rotate-180' : ''}"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		</button>
	{/if}

	<!-- Dropdown panel — portaled to body -->
	{#if open}
		<div
			use:portal
			bind:this={panelRef}
			class="dropdown-panel fixed z-[100] rounded-lg border p-1 overflow-hidden
				{$glassTheme
					? 'glass-dropdown border-white/15'
					: 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
			style="
				{dropPos.openUp ? `bottom: ${window.innerHeight - dropPos.top}px` : `top: ${dropPos.top}px`};
				left: {dropPos.left}px;
				min-width: {dropPos.width}px;
				max-width: min(360px, calc(100vw - 32px));
			"
			role="listbox"
			tabindex="-1"
			aria-activedescendant={highlightIndex >= 0 ? `dropdown-opt-${highlightIndex}` : undefined}
		>
			<div class="max-h-64 overflow-y-auto">
				{#each [...groups] as [groupName, groupOptions], gi}
					{#if hasGroups && groupName}
						<div class="px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wider
							{gi > 0 ? 'mt-1 border-t ' + ($glassTheme ? 'border-white/[0.06]' : 'border-surface-700') : ''}
							text-surface-500">
							{groupName}
						</div>
					{/if}
					{#each groupOptions as opt}
						{@const flatIdx = options.indexOf(opt)}
						{@const isSelected = opt.value === value}
						{@const isHighlighted = flatIdx === highlightIndex}
						<button
							id="dropdown-opt-{flatIdx}"
							data-dropdown-option
							type="button"
							disabled={opt.disabled}
							role="option"
							aria-selected={isSelected}
							onclick={() => select(opt.value)}
							onmouseenter={() => { if (!opt.disabled) highlightIndex = flatIdx; }}
							class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left {textClass} transition-colors
								{opt.disabled
									? 'text-surface-600 cursor-not-allowed'
									: isSelected
										? ($glassTheme
											? 'bg-white/[0.14] text-surface-100 font-medium'
											: 'bg-surface-600 text-surface-100 font-medium')
										: isHighlighted
											? ($glassTheme
												? 'bg-white/[0.08] text-surface-100'
												: 'bg-surface-700 text-surface-100')
											: ($glassTheme
												? 'text-surface-300 hover:bg-white/[0.06]'
												: 'text-surface-300 hover:bg-surface-700')}"
						>
							<!-- Selected check mark -->
							{#if isSelected}
								<svg class="h-3.5 w-3.5 shrink-0 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
								</svg>
							{:else}
								<span class="h-3.5 w-3.5 shrink-0"></span>
							{/if}
							<span class="truncate">{opt.label}</span>
							{#if opt.description}
								<span class="ml-auto truncate text-xs text-surface-500">{opt.description}</span>
							{/if}
						</button>
					{/each}
				{/each}

				{#if options.length === 0}
					<div class="px-3 py-3 text-center text-sm text-surface-500">No options</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.dropdown-panel {
		animation: dropdown-in 150ms ease-out;
		transform-origin: top center;
	}

	@keyframes dropdown-in {
		from {
			opacity: 0;
			transform: translateY(-4px) scale(0.97);
		}
		to {
			opacity: 1;
			transform: translateY(0) scale(1);
		}
	}
</style>
