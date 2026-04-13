<script lang="ts">
	import { getCurrentWindow } from '@tauri-apps/api/window';
	import { platform } from '@tauri-apps/plugin-os';
	import { onMount } from 'svelte';
	import type { Snippet } from 'svelte';

	let { nav, center, right }: { nav?: Snippet; center?: Snippet; right?: Snippet } = $props();

	let currentPlatform = $state('');

	onMount(() => {
		try {
			currentPlatform = platform();
		} catch {
			// Not in Tauri environment (e.g. dev browser) — default to showing custom controls
			currentPlatform = 'linux';
		}
	});

	const appWindow = getCurrentWindow();

	/**
	 * Manual drag handler — more reliable than data-tauri-drag-region.
	 * Handles both single-click drag and double-click maximize.
	 */
	function handleDragMouseDown(e: MouseEvent) {
		if (e.buttons !== 1) return;
		if (e.detail === 2) {
			appWindow.toggleMaximize();
		} else {
			appWindow.startDragging();
		}
	}
</script>

<!-- Fixed titlebar: drag region + browser nav + center content + right content + window controls -->
<div
	class="fixed top-0 left-0 right-0 z-[9999] flex h-[38px] select-none items-center bg-surface-900"
	class:pl-[72px]={currentPlatform === 'macos'}
	class:pl-3={currentPlatform !== 'macos'}
>
	<!-- Browser navigation controls (back / forward / reload) -->
	<div class="flex items-center gap-1 pr-2">
		<div class="flex items-center rounded-lg bg-surface-800 border border-surface-700">
			<button
				class="px-1.5 py-1 text-surface-400 transition-colors hover:text-surface-200"
				onclick={() => history.back()}
				title="Back"
			>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
				</svg>
			</button>
			<span class="h-3.5 w-px bg-surface-700"></span>
			<button
				class="px-1.5 py-1 text-surface-400 transition-colors hover:text-surface-200"
				onclick={() => history.forward()}
				title="Forward"
			>
				<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
				</svg>
			</button>
		</div>
		<button
			class="rounded-lg bg-surface-800 border border-surface-700 px-1.5 py-1 text-surface-400 transition-colors hover:text-surface-200"
			onclick={() => location.reload()}
			title="Reload"
		>
			<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
			</svg>
		</button>
	</div>

	<!-- Primary navigation (Pulse / Omni / Coherence) -->
	{#if nav}
		<div class="flex items-center ml-2">
			{@render nav()}
		</div>
	{/if}

	<!-- Center content (e.g. date navigation) — absolutely centered in the titlebar -->
	{#if center}
		<div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
			{@render center()}
		</div>
	{/if}

	<!-- Drag region — empty space that acts as window drag handle -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="flex-1 h-full" onmousedown={handleDragMouseDown}></div>

	<!-- Right content (e.g. chat, settings, health) -->
	{#if right}
		<div class="flex items-center gap-1 pr-2">
			{@render right()}
		</div>
	{/if}

	<!-- Window controls: only on Windows / Linux (macOS uses native traffic lights) -->
	{#if currentPlatform !== 'macos' && currentPlatform !== ''}
		<div class="flex h-full">
			<button
				onclick={() => appWindow.minimize()}
				class="flex w-[46px] items-center justify-center text-surface-400 hover:bg-surface-700/50 transition-colors"
				aria-label="Minimize"
			>
				<svg width="10" height="10" viewBox="0 0 10 10">
					<rect fill="currentColor" width="10" height="1" y="5" />
				</svg>
			</button>
			<button
				onclick={() => appWindow.toggleMaximize()}
				class="flex w-[46px] items-center justify-center text-surface-400 hover:bg-surface-700/50 transition-colors"
				aria-label="Maximize"
			>
				<svg width="10" height="10" viewBox="0 0 10 10">
					<rect fill="none" stroke="currentColor" stroke-width="1" width="8" height="8" x="1" y="1" />
				</svg>
			</button>
			<button
				onclick={() => appWindow.close()}
				class="flex w-[46px] items-center justify-center text-surface-400 hover:bg-red-500/80 hover:text-white transition-colors"
				aria-label="Close"
			>
				<svg width="10" height="10" viewBox="0 0 10 10">
					<path fill="currentColor" d="M1 0.5L0.5 1L4 5L0.5 9L1 9.5L5 6L9 9.5L9.5 9L6 5L9.5 1L9 0.5L5 4Z" />
				</svg>
			</button>
		</div>
	{/if}
</div>
