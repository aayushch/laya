<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	// Corpus: every document the user shared with Laya. `?doc=ID&page=N`
	// opens one document at that page; otherwise the list with select-and-ask.
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import CorpusList from '$lib/components/corpus/CorpusList.svelte';
	import DocumentView from '$lib/components/corpus/DocumentView.svelte';
	import { documents, documentsLoaded, loadDocuments } from '$lib/stores/documents';
	import { openCorpusSheet, openCorpusSheetWithFiles, openCorpusSheetWithPaths } from '$lib/stores/corpusSheet';
	import { loadSpaces } from '$lib/stores/spaces';
	import { pickFilePaths } from '$lib/utils/fileDrop';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';

	const docId = $derived($page.url.searchParams.get('doc'));
	const docPage = $derived.by(() => {
		const raw = $page.url.searchParams.get('page');
		const n = raw ? Number.parseInt(raw, 10) : NaN;
		return Number.isFinite(n) && n > 0 ? n : null;
	});

	let extensions = $state<string[]>([]);
	let fileInput: HTMLInputElement | null = $state(null);

	onMount(() => {
		loadDocuments();
		loadSpaces();
		engineApi.getSupportedDocumentTypes().then((t) => (extensions = t.extensions)).catch(() => {});
	});

	async function add() {
		const paths = await pickFilePaths(extensions);
		if (paths) {
			openCorpusSheetWithPaths(paths);
			return;
		}
		fileInput?.click();
	}

	function handleFilePicked(e: Event) {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) openCorpusSheetWithFiles(Array.from(target.files));
		target.value = '';
	}
</script>

<svelte:head>
	<title>Corpus - Laya</title>
</svelte:head>

<input
	bind:this={fileInput}
	type="file"
	multiple
	accept={extensions.map((e) => '.' + e).join(',')}
	class="hidden"
	onchange={handleFilePicked}
/>

<div class="mx-auto max-w-4xl px-6 py-6">
	{#if docId}
		<DocumentView documentId={docId} page={docPage} />
	{:else}
		<div class="mb-5 flex items-center justify-between gap-3">
			<div>
				<h1 class="text-lg font-semibold text-surface-100">Corpus</h1>
				<p class="text-laya-secondary text-surface-500">Documents you shared with Laya. Chat answers from them, cards link to them, and Coherence shows them beside related work.</p>
			</div>
			<button
				onclick={add}
				class="flex shrink-0 items-center gap-1.5 rounded-md bg-laya-orange/80 px-3 py-1.5 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-orange"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" /></svg>
				Add
			</button>
		</div>

		{#if !$documentsLoaded}
			<p class="text-laya-base text-surface-500">Loading...</p>
		{:else if $documents.length === 0}
			<div class="flex flex-col items-center gap-3 rounded-xl px-6 py-14 text-center {$glassTheme ? 'glass-section' : 'border border-dashed border-surface-700'}">
				<svg class="h-10 w-10 text-surface-600" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6M7 4h7l5 5v11a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1z" /></svg>
				<p class="text-laya-base text-surface-300">Nothing here yet</p>
				<p class="max-w-md text-laya-secondary text-surface-500">
					Drop a PDF, DOCX, Markdown, text, CSV, JSON or HTML file anywhere in the window, or press Add. Files are read in place and never copied or changed; the extracted text is indexed so chat can answer from it.
				</p>
				<button onclick={openCorpusSheet} class="text-laya-secondary text-laya-orange hover:text-laya-gold">What can I add?</button>
			</div>
		{:else}
			<CorpusList />
		{/if}
	{/if}
</div>
