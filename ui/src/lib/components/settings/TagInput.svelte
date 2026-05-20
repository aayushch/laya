<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';

	let {
		tags = $bindable<string[]>([]),
		placeholder = '',
		onchange
	}: {
		tags?: string[];
		placeholder?: string;
		onchange?: (tags: string[]) => void;
	} = $props();

	let inputValue = $state('');
	let inputEl: HTMLInputElement | undefined = $state();

	function addTag(raw: string) {
		const val = raw.trim().replace(/^#/, '').toLowerCase();
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
	class="flex min-h-[38px] w-full cursor-text flex-wrap items-center gap-1.5 rounded-lg px-3 py-2 text-sm
		{$glassTheme ? 'glass-input' : 'border border-surface-600 bg-surface-700 focus-within:border-laya-orange/50 focus-within:ring-1 focus-within:ring-laya-orange/30'}"
	onclick={focusInput}
>
	{#each tags as tag, i}
		<span class="inline-flex items-center gap-0.5 rounded-full py-0.5 pl-2.5 pr-1 text-xs text-surface-200
			{$glassTheme ? 'bg-white/[0.12]' : 'bg-surface-600'}">
			{tag}
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
		class="min-w-[120px] flex-1 border-none bg-transparent text-sm text-surface-100 outline-none
			{$glassTheme ? 'placeholder:text-surface-400' : 'placeholder:text-surface-500'}"
		onkeydown={handleKeydown}
		oninput={handleInput}
	/>
</div>
<span class="mt-1 block text-xs text-surface-500">Separate channel names with a comma</span>
