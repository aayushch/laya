<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	One entity thread, drawn from its first event to its last. The spine carries
	the latest status colour; each event is a dot on that spine at its own time,
	so a capsule shows both WHEN things happened and HOW LONG the thread stayed
	open. Clicking the body opens the same detail panel the card/list views open;
	clicking a dot selects that specific card.
-->
<script lang="ts">
	import type { Thread } from '$lib/timeline/threads';
	import { statusTone } from '$lib/timeline/threads';
	import { formatMinutes } from '$lib/timeline/scale';
	import { platformDotColor, PRIORITY_LABELS } from '$lib/utils/cardVisuals';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { fade } from 'svelte/transition';
	import { reducedMotion } from '$lib/stores/reducedMotion';

	let {
		thread,
		top,
		height,
		left,
		width,
		compact = false,
		selected = false,
		dimmed = false,
		selectedCardId = '',
		dotY,
		onselect,
		onselectcard,
		onhover,
		onleave
	}: {
		thread: Thread;
		top: number;
		height: number;
		left: number;
		width: number;
		/** At 2h/4h zoom the body degrades to a single line (entity + priority). */
		compact?: boolean;
		selected?: boolean;
		dimmed?: boolean;
		selectedCardId?: string;
		/** Absolute y of an event minute, so dots can be placed inside the capsule. */
		dotY: (minute: number) => number;
		onselect: (thread: Thread) => void;
		onselectcard: (thread: Thread, cardId: string) => void;
		onhover?: (el: HTMLElement, thread: Thread, cardId?: string) => void;
		onleave?: () => void;
	} = $props();

	const tone = $derived(statusTone(thread.latest.status));
	const priorityLabel = $derived(PRIORITY_LABELS[thread.priority] ?? thread.priority);
	// Escalation owns the capsule's colour; the agent glow is the next loudest.
	const escalating = $derived(thread.attention.escalating);
	const agentGlow = $derived(!escalating && (thread.attention.agentRunning || thread.attention.awaitingInput));

	const priorityStyle: Record<string, string> = {
		CRITICAL: 'background: var(--tl-node-failed); color: oklch(0.98 0.01 25);',
		HIGH: 'background: oklch(0.60 0.20 20 / 0.28); color: oklch(0.80 0.14 20);',
		MEDIUM: 'background: oklch(0.70 0.18 58 / 0.22); color: oklch(0.83 0.13 62);',
		LOW: 'background: var(--tl-bg-dormant); color: var(--tl-fg-dormant);'
	};
</script>

<div
	class="tl-capsule absolute overflow-hidden text-left {escalating ? 'tl-capsule--escalating' : ''} {agentGlow ? 'tl-capsule--agent' : ''} {selected ? 'tl-capsule--selected' : ''} {$glassTheme ? 'glass-card-flat' : ''}"
	style="top: {top}px; height: {height}px; left: {left}px; width: {width}px; box-sizing: border-box; {dimmed ? 'opacity: 0.45;' : ''}"
	data-entity-id={thread.entityId}
	data-group-entity={thread.entityId}
	role="button"
	tabindex="0"
	onclick={() => onselect(thread)}
	onkeydown={(e) => {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onselect(thread);
		}
	}}
	onmouseenter={(e) => onhover?.(e.currentTarget as HTMLElement, thread)}
	onmouseleave={() => onleave?.()}
>
	<!-- Spine: the latest event's status colour, running the capsule's full height -->
	<div
		class="pointer-events-none absolute bottom-0 top-0 w-[2px] opacity-55"
		style="left: 8px; background: var(--tl-node-{tone});"
	></div>

	<!-- Event dots, each at its own time, knocked out of the spine -->
	{#each thread.events as event (event.cardId)}
		{@const y = Math.min(Math.max(dotY(event.minute) - top, 5), Math.max(5, height - 13))}
		<button
			data-card-id={event.cardId}
			class="absolute h-[7px] w-[7px] rounded-full transition-transform hover:scale-[1.6]"
			style="left: 5.5px; top: {y}px; background: var(--tl-node-{statusTone(event.status)});
				box-shadow: 0 0 0 2.5px var(--tl-dot-ring){event.cardId === selectedCardId ? ', 0 0 0 4px var(--color-laya-orange)' : ''};"
			transition:fade={{ duration: $reducedMotion ? 0 : 180 }}
			aria-label="{event.statusLabel} at {formatMinutes(event.minute)}"
			onclick={(e) => {
				e.stopPropagation();
				onselectcard(thread, event.cardId);
			}}
			onmouseenter={(e) => {
				e.stopPropagation();
				onhover?.(e.currentTarget as HTMLElement, thread, event.cardId);
			}}
			onmouseleave={() => onleave?.()}
		></button>
	{/each}

	<!-- Header: platform dot · entity · priority -->
	<div class="absolute flex items-center gap-[5px]" style="left: 20px; right: 8px; top: 6px;">
		<span class="h-[5px] w-[5px] shrink-0 rounded-full" style="background-color: {platformDotColor(thread.platform)}"></span>
		<span
			class="min-w-0 truncate font-mono text-[9px] font-semibold"
			style="color: {escalating ? 'var(--tl-escalate-entity)' : 'var(--color-surface-100)'}"
		>{thread.title}</span>
		<span class="ml-auto shrink-0 rounded px-1 py-px text-[8px] font-bold leading-none" style={priorityStyle[thread.priority] ?? priorityStyle.MEDIUM}>
			{priorityLabel}
		</span>
	</div>

	{#if !compact}
		<!-- Title: two-line clamp; the capsule's body text -->
		<div
			class="absolute overflow-hidden text-[10.5px] leading-[1.35]"
			style="left: 20px; right: 8px; top: 22px; color: {escalating ? 'var(--tl-escalate-title)' : 'var(--tl-capsule-title)'};
				display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;"
		>
			{thread.latest.card.header}
		</div>

		<!-- Latest chip: when the thread last moved, in that event's status tint -->
		<div
			class="absolute max-w-[calc(100%-30px)] truncate rounded px-[5px] py-px text-[9.5px] font-semibold"
			style="left: 20px; bottom: 17px; width: fit-content; background: var(--tl-bg-{tone}); color: var(--tl-fg-{tone});"
		>
			{formatMinutes(thread.latest.minute)} · {thread.latest.statusLabel}
		</div>

		<!-- Footer: event count and how long the thread stayed open -->
		<div class="absolute font-mono text-[8px]" style="left: 20px; bottom: 4px; color: var(--tl-capsule-foot)">
			{thread.cardCount} {thread.cardCount === 1 ? 'event' : 'events'} · open {thread.openHours.toFixed(1)}h{thread.carriedForward ? ' · carried' : ''}
		</div>
	{/if}
</div>
