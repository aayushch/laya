<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';

	let {
		tags = $bindable<string[]>([]),
		placeholder = '',
		variant = 'default',
		normalize = (raw: string) => raw.trim().replace(/^#/, '').toLowerCase(),
		prefix = '#',
		onchange
	}: {
		tags?: string[];
		placeholder?: string;
		/** 'accent': an always-focused look with an orange border and ring and
		 *  monospace `#channel` chips, used by the Integrations account card.
		 *  It drops the help line below the box. */
		variant?: 'default' | 'accent';
		/** Turns typed text into a tag. The default suits Slack channel names. */
		normalize?: (raw: string) => string;
		/** Shown before each chip in the 'accent' variant. */
		prefix?: string;
		onchange?: (tags: string[]) => void;
	} = $props();

	let inputValue = $state('');
	let inputEl: HTMLInputElement | undefined = $state();

	function addTag(raw: string) {
		const val = normalize(raw);
		if (!val || tags.includes(val)) return;
		tags = [...tags, val];
		onchange?.(tags);
	}

	function removeTag(index: number) {
		tags = tags.filter((_, i) => i !== index);
		onchange?.(tags);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ',') {
			e.preventDefault();
			if (inputValue.trim()) {
				addTag(inputValue);
				inputValue = '';
			}
		} else if (e.key === 'Backspace' && !inputValue && tags.length > 0) {
			removeTag(tags.length - 1);
		}
	}

	function handleInput() {
		if (inputValue.includes(',')) {
			const parts = inputValue.split(',');
			for (const part of parts.slice(0, -1)) {
				if (part.trim()) addTag(part);
			}
			inputValue = parts[parts.length - 1];
		}
	}

	function focusInput() {
		inputEl?.focus();
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="flex w-full cursor-text flex-wrap items-center gap-1.5
		{variant === 'accent'
			? 'rounded-lg border border-laya-orange bg-integ-input-bg px-2 py-[7px] ring-[3px] ring-integ-accent-tint'
			: $glassTheme
				? 'glass-input min-h-[38px] rounded-lg px-3 py-2 text-sm'
				: 'min-h-[38px] rounded-lg border border-surface-600 bg-surface-700 px-3 py-2 text-sm focus-within:border-laya-orange/50 focus-within:ring-1 focus-within:ring-laya-orange/30'}"
	onclick={focusInput}
>
	{#each tags as tag, i}
		<span class="inline-flex items-center gap-0.5
			{variant === 'accent'
				? 'rounded-[5px] bg-integ-fill-2 py-[3px] pl-2 pr-1 font-mono text-integ-small text-integ-t1'
				: `rounded-full py-0.5 pl-2.5 pr-1 text-xs text-surface-200 ${$glassTheme ? 'bg-white/[0.12]' : 'bg-surface-600'}`}">
			{variant === 'accent' ? `${prefix}${tag}` : tag}
			<button
				type="button"
				class="ml-0.5 rounded-full p-0.5 text-surface-400 hover:text-surface-200
					{$glassTheme ? 'hover:bg-white/[0.15]' : 'hover:bg-surface-500'}"
				aria-label="Remove {tag}"
				onclick={() => removeTag(i)}
			>
				<svg class="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M3 3l6 6M9 3l-6 6" />
				</svg>
			</button>
		</span>
	{/each}
	<input
		bind:this={inputEl}
		bind:value={inputValue}
		{placeholder}
		class="min-w-[120px] flex-1 border-none bg-transparent outline-none
			{variant === 'accent'
				? 'px-0.5 py-[3px] font-mono text-integ-small text-integ-t1 placeholder:text-integ-t4'
				: `text-sm text-surface-100 ${$glassTheme ? 'placeholder:text-surface-400' : 'placeholder:text-surface-500'}`}"
		onkeydown={handleKeydown}
		oninput={handleInput}
	/>
</div>
{#if variant === 'default'}
	<span class="mt-1 block text-xs text-surface-500">Separate channel names with a comma</span>
{/if}
