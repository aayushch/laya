<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	// One document: title, summary, entity links and the extracted chunks in
	// order with page markers. `?page=N` scrolls to and highlights the first
	// chunk covering that page. Rendering the original file is not part of
	// this view; the extracted text is what the engine indexed.
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { CorpusDocument, DocumentChunk } from '$lib/api/types';
	import { documents, documentProgress, refreshDocument, removeDocument } from '$lib/stores/documents';
	import { askAboutDocuments, pendingCardId } from '$lib/stores/chat';
	import { errorLabel, statusLabel } from '$lib/documents/documentUpdateReducer';
	import MarkdownRender from '$lib/components/MarkdownRender.svelte';
	import { glassTheme } from '$lib/stores/glassTheme';

	let { documentId, page = null }: { documentId: string; page?: number | null } = $props();

	let doc = $state<CorpusDocument | null>(null);
	let chunks = $state<DocumentChunk[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let renaming = $state(false);
	let renameValue = $state('');
	let renameInputEl = $state<HTMLInputElement | undefined>();
	let confirmDelete = $state(false);
	let busy = $state(false);
	let highlightedChunk = $state<string | null>(null);
	let container: HTMLDivElement | undefined = $state();

	// The store copy carries live status; the fetched copy carries links.
	const live = $derived($documents.find((d) => d.document_id === documentId) ?? doc);

	async function load() {
		loading = true;
		error = null;
		try {
			doc = await engineApi.getDocument(documentId);
			const resp = await engineApi.getDocumentChunks(documentId, { limit: 500 });
			chunks = resp.chunks;
			await tick();
			scrollToPage();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load the document';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		documentId;
		load();
	});

	// Reload the chunks when an ingest run for this document finishes.
	let lastStatus = $state<string | null>(null);
	$effect(() => {
		const status = live?.status ?? null;
		if (lastStatus && lastStatus !== 'ready' && status === 'ready') load();
		lastStatus = status;
	});

	function scrollToPage() {
		if (!page) return;
		const target = chunks.find((c) => c.page_start != null && c.page_end != null && c.page_start <= page && page <= c.page_end)
			?? chunks.find((c) => c.page_start != null && c.page_start >= page);
		if (!target) return;
		highlightedChunk = target.chunk_id;
		const el = container?.querySelector<HTMLElement>(`[data-chunk-id="${target.chunk_id}"]`);
		el?.scrollIntoView({ block: 'start', behavior: 'smooth' });
	}

	$effect(() => {
		page;
		if (chunks.length) scrollToPage();
	});

	function back() {
		goto('/corpus');
	}

	function ask() {
		askAboutDocuments([documentId]);
	}

	function startRename() {
		renameValue = live?.title ?? '';
		renaming = true;
		tick().then(() => { renameInputEl?.focus(); renameInputEl?.select(); });
	}

	async function commitRename() {
		if (!renaming) return;
		renaming = false;
		const trimmed = renameValue.trim().slice(0, 300);
		if (!trimmed || trimmed === live?.title) return;
		try {
			doc = await engineApi.renameDocument(documentId, trimmed);
			await refreshDocument(documentId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Rename failed';
		}
	}

	async function reprocess(stage: 'all' | 'embed' | 'summarize' | 'link') {
		busy = true;
		try {
			await engineApi.reprocessDocument(documentId, stage);
			await refreshDocument(documentId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Reprocess failed';
		} finally {
			busy = false;
		}
	}

	async function resync() {
		busy = true;
		try {
			await engineApi.resyncDocument(documentId);
			await refreshDocument(documentId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Re-sync failed';
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!confirmDelete) {
			confirmDelete = true;
			setTimeout(() => (confirmDelete = false), 3000);
			return;
		}
		busy = true;
		try {
			await engineApi.deleteDocument(documentId);
			removeDocument(documentId);
			goto('/corpus');
		} catch (e) {
			error = e instanceof Error ? e.message : 'Delete failed';
			busy = false;
		}
	}

	function openCards(entityId: string) {
		// The feed's related-cards filter is keyed by entity; the simplest path
		// is the first card of that entity via the document's related endpoint.
		engineApi.getDocumentRelated(documentId).then((r) => {
			const card = r.cards.find((c) => c.entity_id === entityId) ?? r.cards[0];
			if (card) {
				pendingCardId.set(card.card_id);
				goto('/feed');
			}
		}).catch(() => {});
	}

	function pageLabel(c: DocumentChunk): string {
		if (c.page_start == null) return '';
		if (c.page_end == null || c.page_end === c.page_start) return `p. ${c.page_start}`;
		return `pp. ${c.page_start}-${c.page_end}`;
	}

	/** Strip the heading prefix the chunker prepends so it is not shown twice. */
	function chunkBody(c: DocumentChunk): string {
		if (c.heading && c.text.startsWith(c.heading + '\n')) return c.text.slice(c.heading.length + 1);
		return c.text;
	}

	const btn = 'flex items-center gap-1 rounded-md px-2 py-1 text-laya-secondary text-surface-400 transition-colors hover:bg-surface-700/50 hover:text-surface-200 disabled:cursor-not-allowed disabled:opacity-40';
</script>

<div bind:this={container} class="space-y-5">
	<div class="flex items-start gap-3">
		<button onclick={back} aria-label="Back to Corpus" class="mt-0.5 shrink-0 rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200">
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" /></svg>
		</button>
		<div class="min-w-0 flex-1">
			{#if renaming}
				<input
					bind:this={renameInputEl}
					bind:value={renameValue}
					onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitRename(); } else if (e.key === 'Escape') { renaming = false; } }}
					onblur={commitRename}
					maxlength={300}
					aria-label="Rename document"
					class="w-full rounded border border-laya-orange/40 {$glassTheme ? 'bg-white/[0.05]' : 'bg-surface-800'} px-2 py-1 text-lg font-semibold text-surface-100 focus:border-laya-orange focus:outline-none"
				/>
			{:else}
				<h1 class="truncate text-lg font-semibold text-surface-100">{live?.title ?? 'Document'}</h1>
			{/if}
			{#if live}
				<div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-laya-micro text-surface-500">
					<span class="rounded border border-surface-600/60 px-1 text-[9px] font-semibold uppercase tracking-wide text-surface-400">{live.file_type}</span>
					<span class={live.status === 'ready' ? 'text-green-400' : live.status === 'failed' ? 'text-red-400' : 'text-laya-orange'}>{statusLabel(live.status, $documentProgress[live.document_id])}</span>
					{#if live.page_count}<span>{live.page_count} page{live.page_count === 1 ? '' : 's'}</span>{/if}
					<span>{live.chunk_count} chunk{live.chunk_count === 1 ? '' : 's'}</span>
					<span class="truncate" title={live.source_path ?? live.original_filename}>{live.original_filename}</span>
					{#if live.source_state === 'changed'}<span class="text-amber-400">Changed on disk</span>
					{:else if live.source_state === 'missing'}<span class="text-amber-400">Source moved</span>
					{:else if live.source_state === 'copy'}<span>Stored copy</span>{/if}
					{#if live.error_code || live.error}<span class={live.status === 'failed' ? 'text-red-400' : 'text-amber-400/80'}>{errorLabel(live.error_code, live.error)}</span>{/if}
				</div>
			{/if}
		</div>
	</div>

	<div class="flex flex-wrap items-center gap-1 border-b pb-3 {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'}">
		<button onclick={ask} disabled={live?.status !== 'ready'} class="{btn} !text-laya-orange hover:!bg-laya-orange/10">
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
			Ask about this
		</button>
		<button onclick={startRename} disabled={!live} class={btn}>Rename</button>
		<button onclick={() => reprocess('all')} disabled={busy || !live || live.source_state === 'missing' || (live.status !== 'ready' && live.status !== 'failed')} class={btn}>Reprocess</button>
		<button onclick={() => reprocess('summarize')} disabled={busy || live?.status !== 'ready'} class={btn}>Re-summarise</button>
		<button onclick={resync} disabled={busy || !live || live.storage_mode !== 'reference' || live.source_state === 'missing'} class={btn}>Re-sync</button>
		{#if live?.needs_reembed}
			<button onclick={() => reprocess('embed')} disabled={busy} class="{btn} !text-amber-400">Re-embed</button>
		{/if}
		<button onclick={remove} disabled={busy} class="{btn} ml-auto {confirmDelete ? '!text-red-400' : 'hover:!text-red-400'}">
			{confirmDelete ? 'Click again to confirm' : 'Delete'}
		</button>
	</div>

	{#if error}
		<p class="text-laya-secondary text-red-400">{error}</p>
	{/if}

	{#if loading && !doc}
		<p class="text-laya-base text-surface-500">Loading...</p>
	{:else if doc}
		{#if live?.summary}
			<section>
				<h2 class="mb-2 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Summary</h2>
				<div class="rounded-xl p-4 {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-800/60'}">
					<MarkdownRender content={live.summary} showCopy={false} class="text-laya-base" />
				</div>
			</section>
		{/if}

		{#if doc.entity_links.length > 0}
			<section>
				<h2 class="mb-2 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Linked work items</h2>
				<div class="flex flex-wrap gap-1.5">
					{#each doc.entity_links as link (link.entity_id)}
						<button
							onclick={() => openCards(link.entity_id)}
							disabled={link.card_count === 0}
							title="{link.link_method} ({Math.round(link.confidence * 100)}%){link.first_page ? `, first on page ${link.first_page}` : ''}"
							class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-laya-micro transition-colors {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-800'} text-surface-300 enabled:hover:border-laya-orange/50 enabled:hover:text-laya-orange disabled:opacity-60"
						>
							<span class="truncate max-w-[260px]">{link.matched_text ?? link.entity_id}</span>
							<span class="text-surface-500">{link.card_count} card{link.card_count === 1 ? '' : 's'}</span>
						</button>
					{/each}
				</div>
			</section>
		{/if}

		<section>
			<h2 class="mb-2 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Extracted text</h2>
			{#if chunks.length === 0}
				<p class="text-laya-base text-surface-500">No text has been indexed for this document yet.</p>
			{:else}
				<div class="space-y-2">
					{#each chunks as c (c.chunk_id)}
						<article
							data-chunk-id={c.chunk_id}
							class="scroll-mt-20 rounded-lg px-3 py-2 transition-colors {highlightedChunk === c.chunk_id ? 'ring-1 ring-laya-orange/60 bg-laya-orange/5' : $glassTheme ? 'bg-white/[0.03]' : 'bg-surface-800/40'}"
						>
							<div class="mb-1 flex items-center gap-2 text-laya-micro text-surface-500">
								<span class="tabular-nums">#{c.chunk_index}</span>
								{#if pageLabel(c)}<span class="tabular-nums">{pageLabel(c)}</span>{/if}
								{#if c.heading}<span class="truncate text-surface-400">{c.heading}</span>{/if}
							</div>
							<p class="whitespace-pre-wrap break-words text-laya-base leading-relaxed text-surface-300">{chunkBody(c)}</p>
						</article>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>
