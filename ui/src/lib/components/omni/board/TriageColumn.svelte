<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniItem } from '$lib/api/types';
	import { platformDotColor, platformLabel, PRIORITY_LABELS } from '$lib/utils/cardVisuals';
	import { livePriority, PRIORITY_RANK, priorityToken, shortAge } from '$lib/omni/layers';
	import { parseBackendDate } from '$lib/utils/datetime';

	let {
		items,
		newKeys,
		onOpen
	}: {
		items: OmniItem[];
		/** item_keys added since the last version the user saw. */
		newKeys: Set<string>;
		onOpen: (item: OmniItem) => void;
	} = $props();

	// Priority rank first, then oldest first. Both read the LIVE state: sorting
	// by the frozen priority would float a since-merged CRITICAL above work that
	// actually needs the user.
	const ordered = $derived.by(() =>
		[...items].sort((a, b) => {
			const rankA = PRIORITY_RANK[livePriority(a).toUpperCase()] ?? 2;
			const rankB = PRIORITY_RANK[livePriority(b).toUpperCase()] ?? 2;
			if (rankA !== rankB) return rankA - rankB;
			const ageA = parseBackendDate(a.live?.oldest_created_at)?.getTime() ?? Infinity;
			const ageB = parseBackendDate(b.live?.oldest_created_at)?.getTime() ?? Infinity;
			return ageA - ageB;
		})
	);

	// Platforms shown per row come from the live per-card counts when available —
	// item.platforms is written at synthesis time and can name a platform whose
	// cards have all since been archived.
	function rowPlatforms(item: OmniItem): string[] {
		const live = Object.keys(item.live?.platform_counts ?? {});
		return (live.length > 0 ? live : item.platforms).slice(0, 3);
	}
</script>

<div
	class="om-rail om-glass flex w-[376px] flex-none flex-col"
	style="border-right: 1px solid var(--om-border);"
>
	<div class="flex flex-none items-center gap-2 px-[15px] pt-3 pb-[9px]">
		<span
			class="flex h-[18px] w-[18px] items-center justify-center rounded-full text-[calc(10px*var(--om-scale))] font-bold"
			style="background: var(--om-attn-badge); color: var(--om-attn-badge-fg);"
			aria-hidden="true">!</span
		>
		<span class="om-title" style="color: var(--om-text);">Triage</span>
		<span class="flex-1"></span>
		<span class="om-hint" style="color: var(--om-text-meta);">by priority, then age</span>
	</div>

	<div class="flex min-h-0 flex-1 flex-col gap-px overflow-y-auto px-2 pb-2.5">
		{#if ordered.length === 0}
			<p class="om-item-t px-2.5 py-2" style="color: var(--om-text-meta);">
				Nothing needs attention. Everything Omni is tracking is either moving or done.
			</p>
		{:else}
			<!-- Deliberately unkeyed. The list is replaced wholesale whenever the
			     snapshot changes and holds no per-item state or transition, so
			     positional matching is correct — and a keyed each throws outright on
			     a duplicate key, which is a whole-page failure for a cosmetic gain.
			     The engine now guarantees unique item_keys, but a stored snapshot
			     should never be able to take the board down. -->
			{#each ordered as item}
				{@const priority = livePriority(item).toUpperCase()}
				{@const token = priorityToken(priority)}
				{@const isNew = item.item_key ? newKeys.has(item.item_key) : false}
				{@const age = shortAge(item.live?.oldest_created_at)}
				<button
					type="button"
					class="om-row relative w-full text-left {isNew ? 'om-row--new' : ''}"
					data-omni-item={item.source_cards[0] ?? ''}
					style="padding: calc(9px * var(--om-density)) 10px calc(9px * var(--om-density)) 13px;"
					onclick={() => onOpen(item)}
				>
					<span
						class="absolute top-2 bottom-2 left-0 w-[2.5px] rounded-sm"
						style="background: var(--om-bar-{token});"
					></span>

					<span class="mb-1 flex items-center gap-[7px]">
						<span
							class="om-badge rounded-[3px] px-[5px] py-[1.5px]"
							style="background: var(--om-pri-{token}-bg); color: var(--om-pri-{token}-fg);"
						>{PRIORITY_LABELS[priority] ?? priority}</span>

						{#each rowPlatforms(item) as platform (platform)}
							<span
								class="om-tag inline-flex items-center gap-1 uppercase"
								style="color: var(--om-text-meta);"
							>
								<span
									class="h-[4.5px] w-[4.5px] rounded-full"
									style="background: {platformDotColor(platform)};"
								></span>{platformLabel(platform)}
							</span>
						{/each}

						<span class="flex-1"></span>

						{#if isNew}
							<span
								class="om-mono rounded-[3px] px-1 py-px text-[calc(7.5px*var(--om-scale))] font-semibold tracking-[0.1em]"
								style="background: var(--om-new-bg); color: var(--om-new-fg);"
							>NEW</span>
						{/if}
						{#if age}
							<span class="om-meta">{age}</span>
						{/if}
					</span>

					<!-- The LLM's aggregate sentence, verbatim. Never reformatted, never
					     count-parsed: the aggregation IS the product. -->
					<span
						class="om-item-t block"
						style="color: {priority === 'HIGH' || priority === 'CRITICAL'
							? 'var(--om-text-strong)'
							: 'var(--om-text-body)'};"
					>{item.text}</span>
				</button>
			{/each}
		{/if}
	</div>
</div>
