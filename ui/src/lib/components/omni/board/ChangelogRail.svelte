<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniChangesResponse, TimelineEntry } from '$lib/api/types';
	import { duration, hhmm, layerLabel } from '$lib/omni/layers';
	import { parseBackendDate } from '$lib/utils/datetime';
	import VersionPicker from './VersionPicker.svelte';

	let {
		changes,
		loading,
		baseVersion,
		displayVersion,
		entries,
		onBaseChange,
		onDisplayChange,
		onFullHistory,
		onOpenItem
	}: {
		changes: OmniChangesResponse | null;
		loading: boolean;
		baseVersion: number;
		displayVersion: number;
		/** Selectable versions from the sampled timeline, newest first. */
		entries: TimelineEntry[];
		onBaseChange: (version: number) => void;
		onDisplayChange: (version: number) => void;
		onFullHistory: () => void;
		onOpenItem: (itemKey: string, section: string) => void;
	} = $props();

	// The base list only offers versions OLDER than the displayed one — comparing
	// against something newer than what you're looking at is meaningless.
	const baseEntries = $derived(entries.filter((e) => e.version < displayVersion));
	const displayEntries = $derived(entries);

	const sinceLabel = $derived.by(() => {
		const at = parseBackendDate(changes?.base_generated_at);
		if (!at) return null;
		return duration(Date.now() - at.getTime());
	});

	type Entry = {
		kind: 'added' | 'folded' | 'resolved';
		glyph: string;
		text: string;
		meta: string;
		itemKey: string;
		section: string;
	};

	// Order: added → folded → resolved. What arrived, what moved, what's finished.
	const rows = $derived.by((): Entry[] => {
		if (!changes) return [];
		const out: Entry[] = [];

		for (const a of changes.added) {
			const bits = [layerLabel(a.section)];
			if (a.source_count) {
				bits.push(`from ${a.source_count} ${a.source_count === 1 ? 'event' : 'events'}`);
			}
			if (a.platforms.length) bits.push(a.platforms.join(', '));
			out.push({
				kind: 'added',
				glyph: '+',
				text: a.text,
				meta: bits.join(' · '),
				itemKey: a.item_key,
				section: a.section
			});
		}

		for (const f of changes.folded) {
			const text = f.to_section
				? `"${f.from_text}" folded into "${f.to_text ?? ''}"`
				: `"${f.from_text}" compressed away`;
			const meta = f.to_section
				? `${layerLabel(f.from_section)} → ${layerLabel(f.to_section)}`
				: `${layerLabel(f.from_section)} · dropped`;
			out.push({
				kind: 'folded',
				glyph: '↓',
				text,
				meta,
				itemKey: f.item_key,
				section: f.to_section ?? f.from_section
			});
		}

		for (const r of changes.resolved) {
			const closed = hhmm(r.resolved_at);
			out.push({
				kind: 'resolved',
				glyph: '✓',
				text: `"${r.text}" resolved`,
				meta: closed ? `${layerLabel(r.section)} · closed ${closed}` : layerLabel(r.section),
				itemKey: r.item_key,
				section: r.section
			});
		}

		return out;
	});

	const KIND_STYLE: Record<Entry['kind'], string> = {
		added: 'background: var(--om-ok-bg); color: var(--om-ok-fg);',
		folded: 'background: var(--om-warn-bg); color: var(--om-warn-fg);',
		resolved: 'background: var(--om-neutral-bg); color: var(--om-neutral-fg);'
	};

	const chips = $derived.by(() => {
		if (!changes) return [];
		return (
			[
				{ kind: 'added' as const, glyph: '+', n: changes.counts.added, label: 'new' },
				{ kind: 'folded' as const, glyph: '↓', n: changes.counts.folded, label: 'folded' },
				{ kind: 'resolved' as const, glyph: '✓', n: changes.counts.resolved, label: 'resolved' }
			] satisfies Array<{ kind: Entry['kind']; glyph: string; n: number; label: string }>
		).filter((c) => c.n > 0);
	});
</script>

<div
	class="om-rail om-glass flex w-[326px] flex-none flex-col"
	style="border-left: 1px solid var(--om-border);"
>
	<!-- z-10 so the version popovers' anchors sit above the entry rows below -->
	<div class="relative z-10 flex flex-none flex-col gap-1 px-[15px] pt-3 pb-2">
		<div class="flex items-center gap-2">
			<span class="om-title" style="color: var(--om-text);">What changed</span>
			<span class="flex-1"></span>
			<VersionPicker
				value={baseVersion}
				entries={baseEntries}
				variant="base"
				caption="Compare against"
				disabled={baseEntries.length === 0}
				onSelect={onBaseChange}
				{onFullHistory}
			/>
			<span class="om-mono text-[calc(10px*var(--om-scale))]" style="color: var(--om-text-faint);">→</span>
			<VersionPicker
				value={displayVersion}
				entries={displayEntries}
				variant="display"
				caption="Show version"
				onSelect={onDisplayChange}
				{onFullHistory}
			/>
		</div>
		<span class="om-pill-t" style="color: var(--om-text-meta);">
			{#if sinceLabel}
				since you last looked, {sinceLabel} ago
			{:else}
				compared with v{baseVersion}
			{/if}
		</span>
	</div>

	{#if chips.length > 0}
		<div class="flex flex-none flex-wrap gap-1.5 px-[15px] pb-2.5">
			{#each chips as chip (chip.kind)}
				<span
					class="om-pill-t inline-flex items-center gap-1.5 rounded-full px-[9px] py-[2.5px] font-semibold"
					style={KIND_STYLE[chip.kind]}
				>
					<span class="om-mono text-[calc(11px*var(--om-scale))]" aria-hidden="true">{chip.glyph}</span>
					{chip.n}
					{chip.label}
				</span>
			{/each}
		</div>
	{/if}

	<div
		class="flex min-h-0 flex-1 flex-col overflow-y-auto"
		style="border-top: 1px solid var(--om-divider);"
	>
		{#if loading}
			<p class="om-pill-t px-[15px] py-3" style="color: var(--om-text-meta);">Reading the diff…</p>
		{:else if rows.length === 0}
			<p class="om-entry-t px-[15px] py-3" style="color: var(--om-text-meta);">
				{#if changes && changes.unsummarized_versions.length > 0}
					<!-- Honest about the gap rather than claiming nothing happened:
					     pre-migration-072 snapshots recorded no diff to read back. -->
					No recorded changes between v{baseVersion} and v{displayVersion}. {changes
						.unsummarized_versions.length}
					{changes.unsummarized_versions.length === 1 ? 'version predates' : 'versions predate'} change tracking.
				{:else}
					Nothing has changed since v{baseVersion}.
				{/if}
			</p>
		{:else}
			{#each rows as row, i (row.kind + row.itemKey + i)}
				<button
					type="button"
					class="om-row flex w-full gap-[9px] rounded-none px-[15px] text-left"
					style="padding-block: calc(9px * var(--om-density));
						border-bottom: 1px solid var(--om-divider);"
					onclick={() => onOpenItem(row.itemKey, row.section)}
				>
					<span
						class="om-mono flex h-[17px] w-[17px] flex-none items-center justify-center rounded-[5px] text-[calc(10px*var(--om-scale))] font-semibold"
						style={KIND_STYLE[row.kind]}
						aria-hidden="true">{row.glyph}</span
					>
					<span class="min-w-0 flex-1">
						<span
							class="om-entry-t block"
							style="color: {row.kind === 'resolved'
								? 'var(--om-text-dim)'
								: 'var(--om-text-strong)'};
								{row.kind === 'resolved'
								? 'text-decoration: line-through; text-decoration-color: var(--om-bar-low);'
								: ''}"
						>{row.text}</span>
						<span class="om-meta mt-[3px] block uppercase">{row.meta}</span>
					</span>
				</button>
			{/each}
		{/if}
	</div>
</div>
