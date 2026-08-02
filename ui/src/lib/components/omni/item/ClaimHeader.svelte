<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniEvidenceCard, OmniItem, OmniLineage } from '$lib/api/types';
	import { duration, num } from '$lib/omni/layers';
	import { parseBackendDate } from '$lib/utils/datetime';

	let {
		item,
		cards,
		lineage,
		sourceCardCount
	}: {
		item: OmniItem | null;
		cards: OmniEvidenceCard[];
		/** null on the legacy ?cards= path — provenance degrades, never blanks. */
		lineage: OmniLineage | null;
		sourceCardCount: number;
	} = $props();

	const platformCount = $derived(
		new Set(cards.map((c) => c.platform).filter(Boolean)).size ||
			(item?.platforms?.length ?? 0)
	);
	const peopleCount = $derived(
		new Set(cards.map((c) => c.actor_name).filter(Boolean)).size
	);

	const span = $derived.by(() => {
		const times = cards
			.map((c) => parseBackendDate(c.created_at)?.getTime())
			.filter((t): t is number => t !== undefined);
		if (times.length < 2) return '';
		return duration(Math.max(...times) - Math.min(...times));
	});

	type Entry = { value: string; label: string };

	const provenance = $derived.by((): Entry[] => {
		const out: Entry[] = [
			{
				value: num(sourceCardCount),
				label: sourceCardCount === 1 ? 'source card' : 'source cards'
			}
		];
		if (platformCount > 0) {
			out.push({ value: String(platformCount), label: platformCount === 1 ? 'platform' : 'platforms' });
		}
		if (peopleCount > 0) {
			out.push({ value: String(peopleCount), label: peopleCount === 1 ? 'person' : 'people' });
		}
		if (span) out.push({ value: span, label: 'span' });
		if (lineage) {
			out.push({ value: `v${lineage.first_version}`, label: 'first appeared' });
			const carried = lineage.versions_carried;
			out.push({
				value: `${carried}${lineage.truncated ? '+' : ''}`,
				label: carried === 1 ? 'synthesis carried through' : 'syntheses carried through'
			});
		}
		return out;
	});
</script>

<div
	class="om-claim om-glass flex-none px-[22px]"
	style="border-bottom: 1px solid var(--om-border);
		padding-top: calc(10px * var(--om-density));
		padding-bottom: calc(10px * var(--om-density));"
>
	<!-- Eyebrow, rule and provenance share one rail. The stats used to own a
	     third row below the claim, which cost ~26px of header for six short
	     numbers; on the rule line they read as the claim's dateline instead. -->
	<div class="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
		<span class="om-micro whitespace-nowrap">The line you clicked</span>
		<span
			class="h-px min-w-[20px] flex-1"
			style="background: linear-gradient(90deg, var(--om-border-soft), transparent);"
		></span>
		{#each provenance as entry (entry.label)}
			<span
				class="om-hint inline-flex items-center gap-1 whitespace-nowrap"
				style="color: var(--om-text-meta);"
			>
				<span
					class="om-mono text-[calc(11px*var(--om-scale))] font-semibold"
					style="color: var(--om-text-strong);">{entry.value}</span
				>{entry.label}
			</span>
		{/each}
	</div>

	{#if item}
		<!-- Verbatim from item.text. The aggregate sentence IS the subject of this
		     page; reformatting it would lose the question on the way to the answer. -->
		<h1 class="om-claim-t max-w-[1080px]" style="color: var(--om-text);">{item.text}</h1>
	{:else}
		<!-- Legacy ?cards= link: there is no claim to show, so say that plainly
		     rather than rendering an empty heading. -->
		<h1 class="om-claim-t max-w-[1080px]" style="color: var(--om-text-dim);">
			{sourceCardCount}
			{sourceCardCount === 1 ? 'card' : 'cards'} from an older Omni link
		</h1>
	{/if}
</div>
