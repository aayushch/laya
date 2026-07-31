<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import type {
		OmniChangesResponse,
		OmniItem,
		OmniSnapshot,
		OmniVolumeResponse,
		TimelineEntry
	} from '$lib/api/types';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount, onDestroy, tick } from 'svelte';
	import { get } from 'svelte/store';
	import type { Unsubscriber } from 'svelte/store';
	import { omniSpace } from '$lib/stores/omniSpace';
	import { cardSize } from '$lib/stores/cardSize';
	import {
		resynthesizingSpaces,
		markResynthesizing,
		clearResynthesizing
	} from '$lib/stores/omniResynthesis';
	import { lastSeenVersion, markVersionSeen } from '$lib/stores/omniView';
	import OmniIdentityBar from '$lib/components/omni/board/OmniIdentityBar.svelte';
	import InstrumentCluster from '$lib/components/omni/board/InstrumentCluster.svelte';
	import TriageColumn from '$lib/components/omni/board/TriageColumn.svelte';
	import CompressionFunnel from '$lib/components/omni/board/CompressionFunnel.svelte';
	import ChangelogRail from '$lib/components/omni/board/ChangelogRail.svelte';

	// Where to scroll back to after returning from an item page.
	const SCROLL_TARGET_KEY = 'laya_omni_scroll_target';

	let snapshot = $state<OmniSnapshot | null>(null);
	let volume = $state<OmniVolumeResponse | null>(null);
	let changes = $state<OmniChangesResponse | null>(null);
	let changesLoading = $state(false);
	let timelineEntries = $state<TimelineEntry[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let activeSpaceId = $state(get(omniSpace));
	let nextSynthesisAt = $state<string | null>(null);

	// null = pinned to the latest snapshot; a number = time-travelled, so an
	// omni_updated must NOT yank the user forward.
	let viewingVersion = $state<number | null>(null);
	// User's chosen comparison base. null = derive it (last-seen, else previous).
	let comparisonBase = $state<number | null>(null);
	// Monotonic load id — drops a stale response (e.g. a slow load for the
	// previous space arriving after a space switch) so it can't clobber the view.
	let _loadSeq = 0;

	const resynthesizing = $derived($resynthesizingSpaces.has(activeSpaceId));
	const latestVersion = $derived(
		timelineEntries.length > 0
			? Math.max(...timelineEntries.map((e) => e.version), snapshot?.version ?? 0)
			: (snapshot?.version ?? 0)
	);
	const isViewingOlder = $derived(
		viewingVersion !== null && snapshot !== null && snapshot.version < latestVersion
	);

	const attentionItems = $derived(
		snapshot?.sections.find((s) => s.type === 'attention')?.items ?? []
	);

	/**
	 * item_keys the user has not seen. Taken from the change summary rather than
	 * diffed client-side: the previous version's items aren't in the browser, and
	 * the engine already recorded exactly what it added.
	 */
	const newKeys = $derived(new Set((changes?.added ?? []).map((a) => a.item_key)));

	// Attention delta vs. the comparison base, from the same recorded diff.
	const attentionDelta = $derived.by(() => {
		if (!changes) return null;
		const added = changes.added.filter((a) => a.section === 'attention').length;
		const gone =
			changes.resolved.filter((r) => r.section === 'attention').length +
			changes.folded.filter((f) => f.from_section === 'attention').length;
		const delta = added - gone;
		return delta === 0 ? null : delta;
	});

	let unsubWs: Unsubscriber;
	let boardEl = $state<HTMLElement | null>(null);

	onMount(async () => {
		await loadSpaces();

		// A `?v=` in the URL means a time-travelled board was linked to, or the
		// item page navigated back to the version its claim came from.
		const urlVersion = Number($page.url.searchParams.get('v'));
		if (Number.isFinite(urlVersion) && urlVersion > 0) viewingVersion = urlVersion;

		await loadOmni(viewingVersion ?? undefined);
		loadTimeline();
		loadVolume();
		syncResynthesisStatus(activeSpaceId);

		const scrollTarget = sessionStorage.getItem(SCROLL_TARGET_KEY);
		if (scrollTarget) {
			sessionStorage.removeItem(SCROLL_TARGET_KEY);
			await tick();
			scrollToItem(scrollTarget);
		}

		// store.subscribe (not $effect) so the state writes inside the loaders
		// aren't tracked — that would re-trigger this listener in a loop.
		// `[` / `]` step the displayed version. Guarded against inputs, and against
		// the modifier form — Cmd+[ / Cmd+] are the layout's history back/forward.
		document.addEventListener('keydown', handleVersionStep);

		unsubWs = lastMessage.subscribe((msg) => {
			if (msg?.type !== 'omni_updated') return;
			const p = msg.payload as { space_id?: string } | undefined;
			if (p?.space_id && p.space_id !== activeSpaceId) return;
			if (viewingVersion !== null) return; // reading history — don't jump
			loadOmni();
			loadTimeline();
			loadVolume();
		});
	});

	onDestroy(() => {
		unsubWs?.();
		document.removeEventListener('keydown', handleVersionStep);
	});

	function handleVersionStep(e: KeyboardEvent) {
		if (e.key !== '[' && e.key !== ']') return;
		if (e.metaKey || e.ctrlKey || e.altKey) return;
		const el = e.target as HTMLElement | null;
		const tag = el?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return;
		const current = snapshot?.version;
		if (!current) return;
		const versions = timelineEntries.map((v) => v.version).sort((a, b) => a - b);
		const idx = versions.indexOf(current);
		if (idx === -1) return;
		const next = e.key === '[' ? versions[idx - 1] : versions[idx + 1];
		if (next === undefined) return;
		e.preventDefault();
		setDisplayVersion(next);
	}

	async function loadOmni(version?: number) {
		const seq = ++_loadSeq;
		try {
			loading = !snapshot;
			error = null;
			const snap = await engineApi.getOmni(activeSpaceId, version);
			if (seq !== _loadSeq) return;
			snapshot = snap;
			loadChanges();
			loadSchedule();
			// Only the live view advances the seen mark; reading history must not
			// silently mark newer versions as seen.
			if (version === undefined && snap.version > 0) {
				markVersionSeen(activeSpaceId, snap.version);
			}
		} catch (e) {
			if (seq !== _loadSeq) return;
			error = e instanceof Error ? e.message : 'Failed to load Omni';
		} finally {
			if (seq === _loadSeq) loading = false;
		}
	}

	/**
	 * Resolve the comparison base: the user's pick, else the last version they
	 * actually saw, else the version before the displayed one. Clamped below the
	 * displayed version so time-travelling backwards can't leave the base ahead
	 * of the board.
	 */
	function resolveBase(displayVersion: number): number {
		const candidates = [comparisonBase, lastSeenVersion(activeSpaceId)].filter(
			(v): v is number => v !== null && v > 0 && v < displayVersion
		);
		if (candidates.length > 0) return Math.max(...candidates);
		const older = timelineEntries
			.map((e) => e.version)
			.filter((v) => v < displayVersion)
			.sort((a, b) => b - a);
		return older[0] ?? Math.max(0, displayVersion - 1);
	}

	async function loadChanges() {
		const snap = snapshot;
		if (!snap || snap.version === 0) {
			changes = null;
			return;
		}
		changesLoading = true;
		const seq = _loadSeq;
		try {
			const resp = await engineApi.getOmniChanges(
				activeSpaceId,
				resolveBase(snap.version),
				snap.version
			);
			if (seq !== _loadSeq) return;
			changes = resp;
		} catch {
			// Non-critical: the rail degrades to its empty state, the board stays up.
			if (seq === _loadSeq) changes = null;
		} finally {
			if (seq === _loadSeq) changesLoading = false;
		}
	}

	async function loadTimeline() {
		try {
			const resp = await engineApi.getOmniTimeline(activeSpaceId);
			// Newest first, deduped — the sampled tiers can repeat a boundary entry.
			const seen = new Set<number>();
			timelineEntries = resp.segments
				.flatMap((s) => s.entries)
				.sort((a, b) => b.version - a.version)
				.filter((e) => (seen.has(e.version) ? false : (seen.add(e.version), true)));
		} catch {
			/* non-critical — the pickers just offer fewer versions */
		}
	}

	/**
	 * Pull the complete version list into the pickers. The sampled timeline only
	 * carries every snapshot for today (hourly for the week, syntheses beyond), so
	 * "full history" needs the unsampled endpoint.
	 */
	async function loadFullHistory() {
		try {
			const resp = await engineApi.getOmniHistory(activeSpaceId, 200);
			const merged = new Map(timelineEntries.map((e) => [e.version, e]));
			for (const s of resp.snapshots) {
				if (!merged.has(s.version)) merged.set(s.version, s);
			}
			timelineEntries = [...merged.values()].sort((a, b) => b.version - a.version);
		} catch {
			/* non-critical — the picker keeps the sampled list it already has */
		}
	}

	async function loadVolume() {
		try {
			volume = await engineApi.getOmniVolume(activeSpaceId, 14);
		} catch {
			/* non-critical — the instruments fall back to snapshot-derived counts */
		}
	}

	async function loadSchedule() {
		try {
			const status = await engineApi.getOmniResynthesisStatus(activeSpaceId);
			nextSynthesisAt = status.next_scheduled_at;
			if (status.in_progress) markResynthesizing(activeSpaceId);
		} catch {
			nextSynthesisAt = null;
		}
	}

	async function syncResynthesisStatus(spaceId: string) {
		try {
			const { in_progress } = await engineApi.getOmniResynthesisStatus(spaceId);
			if (in_progress) markResynthesizing(spaceId);
			else clearResynthesizing(spaceId);
		} catch {
			/* non-critical */
		}
	}

	async function handleResynthesis() {
		markResynthesizing(activeSpaceId);
		try {
			// 202 — runs in the background; the omni_updated WS event clears the flag.
			await engineApi.triggerOmniResynthesis(activeSpaceId);
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'Resynthesis failed';
			if (msg.includes('already in progress')) return;
			clearResynthesizing(activeSpaceId);
			error = msg;
		}
	}

	function setDisplayVersion(version: number) {
		viewingVersion = version === latestVersion ? null : version;
		// Linkable, and the item page's Back lands on the same view.
		const url = new URL($page.url);
		if (viewingVersion === null) url.searchParams.delete('v');
		else url.searchParams.set('v', String(version));
		goto(`${url.pathname}${url.search}`, { replaceState: true, noScroll: true, keepFocus: true });

		// Keep the base strictly older than what's on screen.
		if (comparisonBase !== null && comparisonBase >= version) comparisonBase = null;
		loadOmni(viewingVersion ?? undefined);
	}

	function jumpToLatest() {
		setDisplayVersion(latestVersion);
	}

	function setComparisonBase(version: number) {
		comparisonBase = version;
		loadChanges();
	}

	function switchSpace(spaceId: string) {
		activeSpaceId = spaceId;
		omniSpace.set(spaceId);
		snapshot = null;
		changes = null;
		volume = null;
		timelineEntries = [];
		viewingVersion = null;
		comparisonBase = null;
		loadOmni();
		loadTimeline();
		loadVolume();
		syncResynthesisStatus(spaceId);
	}

	/** Screen B carries the item's identity, not just a bag of card ids. */
	function openItem(item: OmniItem, section: string) {
		if (!snapshot) return;
		if (!item.item_key) {
			// Pre-072 snapshot with no derivable key: fall back to the legacy
			// card-list link so the drill-down still works, just degraded.
			if (item.source_cards.length === 0) return;
			const params = new URLSearchParams();
			item.source_cards.forEach((id) => params.append('cards', id));
			goto(`/omni/insight?${params}`);
			return;
		}
		if (item.source_cards[0]) sessionStorage.setItem(SCROLL_TARGET_KEY, item.source_cards[0]);
		const params = new URLSearchParams({
			v: String(snapshot.version),
			section,
			item: item.item_key,
			space_id: activeSpaceId
		});
		goto(`/omni/insight?${params}`);
	}

	/** Changelog rows carry a key + section but no item object. */
	function openItemKey(itemKey: string, section: string) {
		if (!snapshot || !itemKey) return;
		const params = new URLSearchParams({
			v: String(snapshot.version),
			section,
			item: itemKey,
			space_id: activeSpaceId
		});
		goto(`/omni/insight?${params}`);
	}

	/**
	 * Bring the row the user drilled into back into view when they return.
	 * Retried across frames because the board's columns are still laying out when
	 * this runs — the target may not exist on the first attempt.
	 */
	function scrollToItem(cardId: string) {
		const attempt = (tries: number) => {
			requestAnimationFrame(() => {
				const el = boardEl?.querySelector(`[data-omni-item="${cardId}"]`);
				if (el) {
					el.scrollIntoView({ behavior: 'smooth', block: 'center' });
					el.classList.add('card-highlight-fade');
					el.addEventListener('animationend', () => el.classList.remove('card-highlight-fade'), {
						once: true
					});
				} else if (tries > 0) {
					attempt(tries - 1);
				}
			});
		};
		attempt(5);
	}
</script>

<svelte:head>
	<title>Omni - Laya</title>
</svelte:head>

<!-- Full-bleed: the layout's <main> has p-4, which the negative margin cancels so
     the board's columns reach the window edges as designed. `data-omni-density`
     carries the Card Size setting to the CSS that tightens row padding. -->
<div
	bind:this={boardEl}
	class="-m-4 flex h-[calc(100%+2rem)] flex-col overflow-hidden"
	style="color: var(--om-text);"
	data-omni-density={$cardSize}
>
	{#if error}
		<div
			class="om-entry-t flex-none px-[18px] py-2"
			style="background: var(--om-alert-bg); color: var(--om-alert-fg);"
		>{error}</div>
	{/if}

	{#if loading && !snapshot}
		<div class="flex flex-1 items-center justify-center gap-2">
			<svg class="text-laya-orange h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
				<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
			</svg>
			<span class="om-row-t" style="color: var(--om-text-meta);">Loading Omni…</span>
		</div>
	{:else if snapshot}
		<OmniIdentityBar
			version={snapshot.version}
			generatedAt={snapshot.generated_at}
			snapshotType={snapshot.snapshot_type}
			spaces={$spaces}
			{activeSpaceId}
			{resynthesizing}
			{isViewingOlder}
			onSpaceChange={switchSpace}
			onResynthesis={handleResynthesis}
			onJumpToLatest={jumpToLatest}
		/>

		{#if snapshot.version === 0 && snapshot.sections.length === 0}
			<div class="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
				<div
					class="flex h-16 w-16 items-center justify-center rounded-full"
					style="background: var(--om-instrument);"
				>
					<svg
						class="h-8 w-8"
						style="color: var(--om-text-meta);"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="1.5"
					>
						<path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z" />
						<path d="M12 6v6l4 2" />
					</svg>
				</div>
				<div>
					<h3 class="om-title" style="color: var(--om-text);">Omni is warming up</h3>
					<p class="om-row-t mt-1 max-w-sm" style="color: var(--om-text-meta);">
						As Laya processes events, Omni will build a rolling summary of your professional
						activity across all platforms.
					</p>
				</div>
				<button
					type="button"
					class="om-row-t rounded-lg px-4 py-2 font-medium disabled:opacity-50"
					style="background: var(--om-comp-bg); color: var(--om-comp-num);"
					disabled={resynthesizing}
					onclick={handleResynthesis}
				>{resynthesizing ? 'Synthesizing…' : 'Generate first summary'}</button>
			</div>
		{:else}
			<InstrumentCluster
				{snapshot}
				{volume}
				{attentionItems}
				{attentionDelta}
				{nextSynthesisAt}
				{resynthesizing}
			/>

			<div class="flex min-h-0 flex-1" style="border-top: 1px solid var(--om-border);">
				<TriageColumn
					items={attentionItems}
					{newKeys}
					onOpen={(item) => openItem(item, 'attention')}
				/>
				<CompressionFunnel
					{snapshot}
					changes={changes}
					version={snapshot.version}
					onOpen={openItem}
				/>
				<ChangelogRail
					{changes}
					loading={changesLoading}
					baseVersion={changes?.base_version ?? resolveBase(snapshot.version)}
					displayVersion={snapshot.version}
					entries={timelineEntries}
					onBaseChange={setComparisonBase}
					onDisplayChange={setDisplayVersion}
					onFullHistory={loadFullHistory}
					onOpenItem={openItemKey}
				/>
			</div>
		{/if}
	{/if}
</div>
