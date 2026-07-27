<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Threads that found no free lane. They are NEVER shifted in time to fit — the
	strip keeps them at their true minutes as thin bars, and clicking it widens
	the lanes so they can be read properly.
-->
<script lang="ts">
	import type { Thread } from '$lib/timeline/threads';
	import { statusTone } from '$lib/timeline/threads';
	import type { TimeScale } from '$lib/timeline/scale';
	import { formatMinutes } from '$lib/timeline/scale';

	let {
		threads = [],
		scale,
		width = 50,
		onexpand,
		onhover,
		onleave
	}: {
		threads?: { thread: Thread; startMin: number; endMin: number }[];
		scale: TimeScale;
		width?: number;
		onexpand: () => void;
		onhover?: (el: HTMLElement, text: string) => void;
		onleave?: () => void;
	} = $props();
</script>

<div
	class="tl-glass-surface absolute bottom-0 top-0 border-l border-dashed"
	style="right: 0; width: {width}px; border-color: var(--tl-strip-border); background: var(--tl-rail-bg);"
>
	<button
		class="absolute inset-0 h-full w-full cursor-pointer"
		onclick={onexpand}
		title="Show {threads.length} overflowed {threads.length === 1 ? 'thread' : 'threads'} in extra lanes"
		aria-label="Expand overflow lanes"
	>
		<span class="absolute inset-x-0 top-[5px] text-center font-mono text-[9px] font-semibold" style="color: var(--color-surface-400)">
			+{threads.length}
		</span>
		<span class="absolute inset-x-0 top-[19px] text-center text-[7.5px] leading-[1.35]" style="color: var(--tl-micro)">
			low<br />signal
		</span>
	</button>

	{#each threads as item, i (item.thread.key)}
		{@const top = scale.y(item.startMin)}
		{@const height = Math.max(14, scale.y(item.endMin) - top)}
		<!-- Each bar is itself the expand affordance: it sits above the full-strip
		     button, so a click landing on a bar would otherwise do nothing. -->
		<button
			class="absolute w-1 rounded-[2px] opacity-[0.32] transition-opacity hover:opacity-90"
			style="top: {top}px; height: {height}px; left: {8 + (i % 6) * 7}px; background: var(--tl-node-{statusTone(item.thread.latest.status)});"
			aria-label="{item.thread.title} — expand overflow lanes"
			onclick={onexpand}
			onmouseenter={(e) =>
				onhover?.(
					e.currentTarget as HTMLElement,
					`${item.thread.title} · ${formatMinutes(item.startMin)}–${formatMinutes(item.endMin)} · ${item.thread.cardCount} events`
				)}
			onmouseleave={() => onleave?.()}
		></button>
	{/each}
</div>
