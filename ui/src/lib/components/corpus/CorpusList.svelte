<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	// Every Corpus document, grouped by space, with multi-select and a sticky
	// action bar ("Ask about N documents", delete). Row badges show the source
	// state of referenced files (changed on disk, moved) with the matching action.
	import { goto } from '$app/navigation';
	import { engineApi } from '$lib/api/engine';
	import type { CorpusDocument } from '$lib/api/types';
	import { documents, documentProgress, removeDocument, refreshDocument } from '$lib/stores/documents';
	import { spaces } from '$lib/stores/spaces';
	import { askAboutDocuments } from '$lib/stores/chat';
	import { errorLabel, statusLabel } from '$lib/documents/documentUpdateReducer';
	import { pickFilePaths } from '$lib/utils/fileDrop';
	import { parseBackendDate } from '$lib/utils/datetime';
	import { glassTheme } from '$lib/stores/glassTheme';

	let selected = $state(new Set<string>());
	let confirmDeleteId = $state<string | null>(null);
	let busyId = $state<string | null>(null);
	let rowError = $state<Record<string, string>>({});

	const groups = $derived.by(() => {
		const bySpace = new Map<string, CorpusDocument[]>();
		for (const d of $documents) {
			const list = bySpace.get(d.space_id) ?? [];
			list.push(d);
			bySpace.set(d.space_id, list);
		}
		const order = [...bySpace.keys()].sort((a, b) => {
			if (a === 'default') return -1;
			if (b === 'default') return 1;
			return spaceName(a).localeCompare(spaceName(b));
		});
		return order.map((id) => ({ space_id: id, name: spaceName(id), docs: bySpace.get(id)! }));
	});

	function spaceName(id: string): string {
		return $spaces.find((s) => s.space_id === id)?.name ?? (id === 'default' ? 'Default' : id);
	}

	const selectedReady = $derived([...selected].filter((id) => $documents.find((d) => d.document_id === id)?.status === 'ready'));

	function toggle(id: string) {
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selected = next;
	}

	function clearSelection() {
		selected = new Set();
	}

	function open(doc: CorpusDocument) {
		goto(`/corpus?doc=${encodeURIComponent(doc.document_id)}`);
	}

	function ask(ids: string[]) {
		askAboutDocuments(ids);
		clearSelection();
	}

	async function remove(id: string) {
		if (confirmDeleteId !== id) {
			confirmDeleteId = id;
			setTimeout(() => { if (confirmDeleteId === id) confirmDeleteId = null; }, 3000);
			return;
		}
		confirmDeleteId = null;
		busyId = id;
		try {
			await engineApi.deleteDocument(id);
			removeDocument(id);
			const next = new Set(selected);
			next.delete(id);
			selected = next;
		} catch (e) {
			rowError = { ...rowError, [id]: e instanceof Error ? e.message : 'Delete failed' };
		} finally {
			busyId = null;
		}
	}

	async function deleteSelected() {
		for (const id of [...selected]) {
			confirmDeleteId = id;
			await remove(id);
		}
	}

	async function reprocess(id: string, stage: 'all' | 'embed' | 'summarize' | 'link' = 'all') {
		busyId = id;
		try {
			await engineApi.reprocessDocument(id, stage);
			await refreshDocument(id);
		} catch (e) {
			rowError = { ...rowError, [id]: e instanceof Error ? e.message : 'Reprocess failed' };
		} finally {
			busyId = null;
		}
	}

	async function resync(id: string) {
		busyId = id;
		try {
			await engineApi.resyncDocument(id);
			await refreshDocument(id);
		} catch (e) {
			rowError = { ...rowError, [id]: e instanceof Error ? e.message : 'Re-sync failed' };
		} finally {
			busyId = null;
		}
	}

	let relocateInput: HTMLInputElement | null = $state(null);
	let relocateTarget = $state<string | null>(null);

	async function locate(doc: CorpusDocument) {
		const ext = doc.original_filename.split('.').pop()?.toLowerCase();
		const paths = await pickFilePaths(ext ? [ext] : []);
		if (paths && paths.length > 0) {
			await relocateTo(doc.document_id, paths[0]);
			return;
		}
		// Browser: the file input only yields a name, so relocation needs the
		// desktop app; surface that instead of failing silently.
		rowError = { ...rowError, [doc.document_id]: 'Locate the file from the desktop app.' };
	}

	async function relocateTo(id: string, path: string) {
		busyId = id;
		try {
			await engineApi.relocateDocument(id, path);
			await refreshDocument(id);
			const next = { ...rowError };
			delete next[id];
			rowError = next;
		} catch (e) {
			rowError = { ...rowError, [id]: e instanceof Error ? e.message : 'Relocate failed' };
		} finally {
			busyId = null;
		}
	}

	function sizeLabel(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}

	function dateLabel(ts: string): string {
		const d = parseBackendDate(ts);
		return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '';
	}

	function statusTone(doc: CorpusDocument): string {
		if (doc.status === 'ready') return doc.error_code ? 'text-amber-400' : 'text-green-400';
		if (doc.status === 'failed') return 'text-red-400';
		return 'text-laya-orange';
	}
</script>

<div class="space-y-6">
	{#each groups as group (group.space_id)}
		<section>
			{#if groups.length > 1 || group.space_id !== 'default'}
				<h2 class="mb-2 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">{group.name}</h2>
			{/if}
			<ul class="divide-y {$glassTheme ? 'divide-white/[0.06]' : 'divide-surface-700/60'} rounded-xl {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-800/60'}">
				{#each group.docs as doc (doc.document_id)}
					{@const prog = $documentProgress[doc.document_id]}
					<li class="group flex items-start gap-3 px-3 py-2.5">
						<input
							type="checkbox"
							aria-label="Select {doc.title}"
							checked={selected.has(doc.document_id)}
							onchange={() => toggle(doc.document_id)}
							class="mt-1 h-3.5 w-3.5 shrink-0 cursor-pointer accent-[var(--color-laya-orange)]"
						/>
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2">
								<button onclick={() => open(doc)} class="min-w-0 truncate text-left text-laya-base font-medium text-surface-100 transition-colors hover:text-laya-orange">
									{doc.title}
								</button>
								<span class="shrink-0 rounded border border-surface-600/60 px-1 text-[9px] font-semibold uppercase tracking-wide text-surface-400">{doc.file_type}</span>
								<span class="ml-auto shrink-0 text-laya-micro font-medium {statusTone(doc)}">{statusLabel(doc.status, prog)}</span>
							</div>
							<div class="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-laya-micro text-surface-500">
								{#if doc.page_count}<span>{doc.page_count} page{doc.page_count === 1 ? '' : 's'}</span>{/if}
								<span>{doc.chunk_count} chunk{doc.chunk_count === 1 ? '' : 's'}</span>
								<span>{sizeLabel(doc.size_bytes)}</span>
								<span>{dateLabel(doc.created_at)}</span>
								{#if doc.source_state === 'changed'}
									<span class="inline-flex items-center gap-1 text-amber-400">
										Changed on disk
										<button onclick={() => resync(doc.document_id)} disabled={busyId === doc.document_id} class="underline underline-offset-2 hover:text-amber-300 disabled:opacity-50">Re-sync</button>
									</span>
								{:else if doc.source_state === 'missing'}
									<span class="inline-flex items-center gap-1 text-amber-400">
										Source moved
										<button onclick={() => locate(doc)} disabled={busyId === doc.document_id} class="underline underline-offset-2 hover:text-amber-300 disabled:opacity-50">Locate</button>
									</span>
								{:else if doc.source_state === 'copy'}
									<span>Stored copy</span>
								{/if}
								{#if doc.needs_reembed}
									<span class="inline-flex items-center gap-1 text-amber-400">
										Embedding model changed
										<button onclick={() => reprocess(doc.document_id, 'embed')} disabled={busyId === doc.document_id} class="underline underline-offset-2 hover:text-amber-300 disabled:opacity-50">Re-embed</button>
									</span>
								{/if}
							</div>
							{#if doc.error_code || doc.error}
								<p class="mt-0.5 text-laya-micro {doc.status === 'failed' ? 'text-red-400' : 'text-amber-400/80'}">{errorLabel(doc.error_code, doc.error)}</p>
							{/if}
							{#if rowError[doc.document_id]}
								<p class="mt-0.5 text-laya-micro text-red-400">{rowError[doc.document_id]}</p>
							{/if}
							{#if doc.summary && doc.status === 'ready'}
								<p class="mt-1 line-clamp-2 text-laya-secondary text-surface-400">{doc.summary.replace(/^#+\s*/gm, '').replace(/\n+/g, ' ')}</p>
							{/if}
						</div>
						<div class="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
							<button
								onclick={() => ask([doc.document_id])}
								disabled={doc.status !== 'ready'}
								aria-label="Ask about this document"
								title="Ask about this"
								class="rounded p-1 text-surface-500 transition-colors hover:text-laya-orange disabled:opacity-30"
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
							</button>
							<button
								onclick={() => reprocess(doc.document_id, 'all')}
								disabled={busyId === doc.document_id || doc.source_state === 'missing' || (doc.status !== 'ready' && doc.status !== 'failed')}
								aria-label="Reprocess"
								title="Reprocess"
								class="rounded p-1 text-surface-500 transition-colors hover:text-laya-orange disabled:opacity-30"
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
							</button>
							<button
								onclick={() => remove(doc.document_id)}
								disabled={busyId === doc.document_id}
								aria-label={confirmDeleteId === doc.document_id ? 'Click again to confirm delete' : 'Delete'}
								title={confirmDeleteId === doc.document_id ? 'Click again to confirm' : 'Delete'}
								class="rounded p-1 transition-colors disabled:opacity-30 {confirmDeleteId === doc.document_id ? 'text-red-400' : 'text-surface-500 hover:text-red-400'}"
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
							</button>
						</div>
					</li>
				{/each}
			</ul>
		</section>
	{/each}
</div>

{#if selected.size > 0}
	<div class="sticky bottom-4 mt-4 flex items-center gap-2 rounded-xl px-4 py-2.5 shadow-xl {$glassTheme ? 'glass-section' : 'border border-surface-600 bg-surface-800'}">
		<span class="text-laya-secondary text-surface-300">{selected.size} selected</span>
		<button onclick={clearSelection} class="text-laya-micro text-surface-500 hover:text-surface-300">Clear</button>
		<div class="ml-auto flex items-center gap-2">
			<button
				onclick={deleteSelected}
				class="rounded-md px-3 py-1.5 text-laya-secondary text-surface-400 transition-colors hover:text-red-400"
			>Delete</button>
			<button
				onclick={() => ask(selectedReady)}
				disabled={selectedReady.length === 0}
				title={selectedReady.length === 0 ? 'Select at least one ready document' : ''}
				class="rounded-md bg-laya-orange/80 px-3 py-1.5 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-orange disabled:cursor-not-allowed disabled:opacity-40"
			>
				Ask about {selectedReady.length === 1 ? '1 document' : `${selectedReady.length} documents`}
			</button>
		</div>
	</div>
{/if}
