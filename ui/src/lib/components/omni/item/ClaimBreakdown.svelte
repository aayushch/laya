<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniBucket, OmniEvidenceCard } from '$lib/api/types';
	import { BUCKET_LABELS, BUCKET_TOKEN, activeBuckets, bucketCounts, cardBucket } from '$lib/omni/buckets';
	import { actorAvatarColor, actorInitials } from '$lib/utils/cardVisuals';
	import { hhmm } from '$lib/omni/layers';
	import { parseBackendDate } from '$lib/utils/datetime';

	let {
		cards,
		activeFilter,
		onFilter,
		onSelectCard
	}: {
		cards: OmniEvidenceCard[];
		activeFilter: OmniBucket | null;
		onFilter: (bucket: OmniBucket | null) => void;
		onSelectCard: (cardId: string) => void;
	} = $props();

	// Derived from live card state, never from parsing the LLM's sentence. The
	// sentence stays untouched prose above; these are the structure under it.
	const counts = $derived(bucketCounts(cards));
	const buckets = $derived(activeBuckets(counts));
	const total = $derived(cards.length);

	// --- Mini timeline: one dot per card, positioned by created_at ---
	const times = $derived(
		cards
			.map((c) => ({ card: c, t: parseBackendDate(c.created_at)?.getTime() }))
			.filter((e): e is { card: OmniEvidenceCard; t: number } => e.t !== undefined)
			.sort((a, b) => a.t - b.t)
	);
	const first = $derived(times[0]?.t ?? 0);
	const last = $derived(times[times.length - 1]?.t ?? 0);
	const spanMs = $derived(Math.max(1, last - first));

	const rangeLabel = $derived.by(() => {
		if (times.length === 0) return '';
		const start = hhmm(times[0].card.created_at);
		const end = hhmm(times[times.length - 1].card.created_at);
		const startDate = new Date(first);
		const isToday = new Date().toDateString() === startDate.toDateString();
		const dayPart = isToday
			? 'today'
			: startDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
		return start === end ? `${start} · ${dayPart}` : `${start} → ${end} · ${dayPart}`;
	});

	// --- Actors ---
	const actors = $derived.by(() => {
		const counted = new Map<string, number>();
		for (const card of cards) {
			const name = card.actor_name?.trim();
			if (!name) continue;
			counted.set(name, (counted.get(name) ?? 0) + 1);
		}
		return [...counted.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
	});
</script>

<div
	class="flex flex-none gap-2.5 px-[22px]"
	style="border-bottom: 1px solid var(--om-border);
		padding-top: calc(9px * var(--om-density));
		padding-bottom: calc(9px * var(--om-density));"
>
	<!-- Left: the claim, broken down — the page's primary control -->
	<div class="flex min-w-0 flex-[1.35] flex-col gap-1.5">
		{#if total > 0}
			<!-- No caption: the bar and its pills are the same colours, and the pills
			     are plainly buttons — a "click to filter" instruction line cost a row
			     of header to say what the control already says. -->
			<div class="flex h-[6px] gap-0.5 overflow-hidden rounded">
				{#each buckets as bucket (bucket)}
					<span
						style="flex: {counts[bucket]};
							background: var(--om-{BUCKET_TOKEN[bucket]}-dot);
							opacity: {activeFilter === null || activeFilter === bucket ? 1 : 0.55};"
					></span>
				{/each}
			</div>

			<div class="flex flex-wrap gap-[6px]">
				{#each buckets as bucket (bucket)}
					{@const active = activeFilter === bucket}
					<button
						type="button"
						class="om-pill-t inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] transition-colors"
						style="background: var(--om-{BUCKET_TOKEN[bucket]}-bg);
							color: var(--om-{BUCKET_TOKEN[bucket]}-fg);
							border: 1px solid {active ? `var(--om-${BUCKET_TOKEN[bucket]}-dot)` : 'transparent'};"
						aria-pressed={active}
						title={active
							? `Showing only ${BUCKET_LABELS[bucket]} — click to clear`
							: `Filter to ${BUCKET_LABELS[bucket]}`}
						onclick={() => onFilter(active ? null : bucket)}
					>
						<span
							class="h-1.5 w-1.5 rounded-full"
							style="background: var(--om-{BUCKET_TOKEN[bucket]}-dot);"
						></span>
						<span class="om-mono font-semibold">{counts[bucket]}</span>
						{BUCKET_LABELS[bucket]}
					</button>
				{/each}
			</div>
		{:else}
			<span class="om-pill-t" style="color: var(--om-text-meta);">
				No evidence cards could be loaded for this line.
			</span>
		{/if}
	</div>

	<!-- Right: when it happened, and who -->
	<div
		class="flex min-w-0 flex-1 flex-col gap-1.5 pl-4"
		style="border-left: 1px solid var(--om-border);"
	>
		<div class="flex items-center gap-2">
			<span class="om-micro whitespace-nowrap">When it happened</span>
			<span class="flex-1"></span>
			<span class="om-mono text-[calc(9px*var(--om-scale))] whitespace-nowrap" style="color: var(--om-text-faint);">
				{rangeLabel}
			</span>
		</div>

		<!-- 18px: the dots carry the meaning, the empty air above them didn't. -->
		<div class="relative h-[18px]">
			<div
				class="absolute right-0 bottom-[7px] left-0 h-px"
				style="background: var(--om-track);"
			></div>
			{#each times as entry (entry.card.card_id)}
				{@const bucket = cardBucket(entry.card)}
				<!-- Clamped to 1–99% so the first and last dots stay fully visible -->
				{@const pct = Math.max(1, Math.min(99, ((entry.t - first) / spanMs) * 100))}
				<button
					type="button"
					class="absolute bottom-[3px] h-2 w-2 -translate-x-1/2 rounded-full"
					style="left: {pct}%;
						background: var(--om-{BUCKET_TOKEN[bucket]}-dot);
						box-shadow: 0 0 0 2.5px var(--om-bar);"
					aria-label="{entry.card.header} — {hhmm(entry.card.created_at)}"
					title="{hhmm(entry.card.created_at)} · {entry.card.header}"
					onclick={() => onSelectCard(entry.card.card_id)}
				></button>
			{/each}
		</div>

		{#if actors.length > 0}
			<div class="flex flex-wrap gap-x-2.5 gap-y-0.5">
				{#each actors as [name, count] (name)}
					<span
						class="om-hint inline-flex items-center gap-1.5"
						style="color: var(--om-text-meta);"
					>
						<span
							class="flex h-[14px] w-[14px] items-center justify-center rounded-full text-[calc(8px*var(--om-scale))] font-semibold text-white"
							style="background: {actorAvatarColor(name)};"
						>{actorInitials(name)}</span>
						{name}
						<span class="om-mono" style="color: var(--om-text-body);">{count}</span>
					</span>
				{/each}
			</div>
		{/if}
	</div>
</div>
