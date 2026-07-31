<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniChangeSummary, OmniItem, OmniSnapshot } from '$lib/api/types';
	import { LAYERS, num } from '$lib/omni/layers';
	import OmniTooltip, { anchorTooltip, type TooltipState } from '../OmniTooltip.svelte';

	let {
		snapshot,
		changes,
		version,
		onOpen
	}: {
		snapshot: OmniSnapshot;
		/** Merged changes over the comparison range — drives the fold annotations. */
		changes: OmniChangeSummary | null;
		version: number;
		onOpen: (item: OmniItem, section: string) => void;
	} = $props();

	let tooltip = $state<TooltipState | null>(null);

	const bands = $derived.by(() =>
		LAYERS.map((layer) => {
			const section = snapshot.sections.find((s) => s.type === layer.type);
			const items = section?.items ?? [];
			return {
				layer,
				items,
				events: items.reduce((sum, i) => sum + i.source_cards.length, 0)
			};
		})
	);

	/**
	 * The annotation that renders BELOW a band: what left this layer between the
	 * comparison base and the displayed version. This is the mechanism made
	 * visible — the reason the funnel layout exists at all. Rendered only when
	 * non-zero, so a quiet period shows a clean funnel rather than three zeros.
	 */
	function annotation(sectionType: string): string | null {
		if (!changes) return null;
		const folded = changes.folded.filter((f) => f.from_section === sectionType);
		const resolved = changes.resolved.filter((r) => r.section === sectionType);
		const parts: string[] = [];

		const promoted = folded.filter((f) => f.to_section);
		if (promoted.length > 0) {
			// Group by destination so "4 folded into This Week" stays one line even
			// when the same band also promoted something to Milestones.
			const byTarget = new Map<string, number>();
			for (const f of promoted) {
				const key = f.to_section as string;
				byTarget.set(key, (byTarget.get(key) ?? 0) + 1);
			}
			for (const [target, count] of byTarget) {
				const verb = target === 'milestone' ? 'promoted to' : 'folded into';
				const noun = count === 1 ? 'item' : 'items';
				const title = LAYERS.find((l) => l.type === target)?.title ?? target;
				parts.push(`${count} ${noun} ${verb} ${title}`);
			}
		}

		const dropped = folded.filter((f) => !f.to_section);
		if (dropped.length > 0) {
			parts.push(`${dropped.length} ${dropped.length === 1 ? 'item' : 'items'} compressed away`);
		}
		if (resolved.length > 0) {
			parts.push(
				`${resolved.length} ${resolved.length === 1 ? 'item' : 'items'} resolved and dropped at v${version}`
			);
		}
		return parts.length > 0 ? parts.join(' · ') : null;
	}
</script>

<div class="flex min-w-0 flex-1 flex-col items-center overflow-y-auto px-4 pt-3 pb-3.5">
	<div class="mb-[9px] flex w-full items-center gap-[9px] self-stretch">
		<span class="om-micro whitespace-nowrap">Compression funnel</span>
		<span
			class="h-px flex-1"
			style="background: linear-gradient(90deg, var(--om-border-card), transparent);"
		></span>
		<span class="om-hint whitespace-nowrap" style="color: var(--om-text-faint);">
			recent → period → milestone → gone
		</span>
	</div>

	{#each bands as band, i (band.layer.type)}
		{@const isAttention = band.layer.type === 'attention'}
		<div
			class="{isAttention ? 'om-glass' : 'om-band om-glass'} flex-none rounded-[9px]"
			style="width: {band.layer.width};
				padding: calc(9px * var(--om-density)) 12px;
				{isAttention
					? 'border: 1px solid var(--om-attn-border); background: var(--om-attn-band-bg);'
					: ''}"
		>
			<div class="mb-[7px] flex items-center gap-2">
				<span
					class="h-1.5 w-1.5 rounded-sm"
					style="background: var(--om-layer-{band.layer.token});"
				></span>
				<span class="om-band-t" style="color: var(--om-layer-{band.layer.token}-fg);">
					{band.layer.title}
				</span>
				<span class="om-mono text-[calc(9px*var(--om-scale))]" style="color: var(--om-text-faint);">
					{band.layer.window}
				</span>
				<span class="flex-1"></span>
				<span
					class="om-mono text-[calc(9px*var(--om-scale))] whitespace-nowrap"
					style="color: var(--om-text-meta);"
				>
					{band.items.length}
					{band.items.length === 1 ? 'line' : 'lines'}{band.events
						? ` · ${num(band.events)} events`
						: ''}
				</span>
			</div>

			{#if band.items.length === 0}
				<p class="om-pill-t" style="color: var(--om-text-faint);">
					{band.layer.type === 'attention' ? 'Clear.' : 'Nothing here yet.'}
				</p>
			{:else}
				<div class="flex flex-col gap-1">
					<!-- Unkeyed for the same reason as the triage list: a duplicate key
					     is a hard render error, and these pills carry no state worth
					     preserving across a snapshot change. -->
					{#each band.items as item}
						<!-- One line, ellipsised, on purpose: the funnel is for SHAPE, the
						     item page is for text. Full text arrives on hover. -->
						<button
							type="button"
							class="om-item-pill om-pill-t w-full px-2 py-[5px] text-left"
							data-omni-item={item.source_cards[0] ?? ''}
							style="color: var(--om-text-body);"
							onclick={() => onOpen(item, band.layer.type)}
							onmouseenter={(e) => (tooltip = anchorTooltip(e.currentTarget, item.text))}
							onmouseleave={() => (tooltip = null)}
							onfocus={(e) => (tooltip = anchorTooltip(e.currentTarget, item.text))}
							onblur={() => (tooltip = null)}
						>{item.text}</button>
					{/each}
				</div>
			{/if}
		</div>

		{#if i < bands.length - 1}
			{@const note = annotation(band.layer.type)}
			{#if note}
				<div
					class="flex items-center gap-[7px] py-[5px] text-[calc(9.5px*var(--om-scale))]"
					style="color: var(--om-text-meta);"
				>
					<span class="om-mono text-[calc(11px*var(--om-scale))]" aria-hidden="true">↓</span>
					{note}
				</div>
			{:else}
				<div class="h-2.5"></div>
			{/if}
		{/if}
	{/each}

	<!-- The tail of the chain: what left Milestones is what left Omni entirely. -->
	{#if annotation('milestone')}
		<div
			class="flex items-center gap-[7px] py-[5px] text-[calc(9.5px*var(--om-scale))]"
			style="color: var(--om-text-meta);"
		>
			<span class="om-mono text-[calc(11px*var(--om-scale))]" aria-hidden="true">↓</span>
			{annotation('milestone')}
		</div>
	{/if}
</div>

<OmniTooltip {tooltip} />
