<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	// The "Add to Corpus" sheet: one tile per dropped or picked file, a space
	// picker, inline errors, and live ingest progress from the documents store.
	// With a single space the files are submitted as soon as the sheet opens;
	// with several the user picks the space first.
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { fade, fly } from 'svelte/transition';
	import { corpusSheet, closeCorpusSheet, updateSheetItem, removeSheetItem, type SheetItem } from '$lib/stores/corpusSheet';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import { feedFilters } from '$lib/stores/feedFilters';
	import { documents, documentProgress } from '$lib/stores/documents';
	import { askAboutDocuments } from '$lib/stores/chat';
	import { uploadFile, uploadPath } from '$lib/documents/upload';
	import { errorLabel, statusLabel } from '$lib/documents/documentUpdateReducer';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import type { CorpusDocument } from '$lib/api/types';

	let spaceId = $state('');
	let title = $state('');
	let submittedOnce = $state(false);

	const open = $derived($corpusSheet.open);
	const items = $derived($corpusSheet.items);
	const pending = $derived(items.filter((it) => !it.documentId && !it.error && !it.submitting));
	const submitted = $derived(items.filter((it) => !!it.documentId));

	function docFor(it: SheetItem): CorpusDocument | undefined {
		return it.documentId ? $documents.find((d) => d.document_id === it.documentId) : undefined;
	}

	const allReady = $derived(
		submitted.length > 0 && pending.length === 0 && submitted.every((it) => docFor(it)?.status === 'ready')
	);
	const anyFailed = $derived(submitted.some((it) => docFor(it)?.status === 'failed'));

	// Default space: the single selected feed space, else the default space.
	$effect(() => {
		if (!open) return;
		if ($spaces.length === 0) loadSpaces();
		if (!spaceId) {
			const sel = get(feedFilters).spaceFilter;
			spaceId = sel.length === 1 ? sel[0] : 'default';
		}
	});

	// Exactly one space: nothing to choose, so submit as soon as items arrive.
	// While the spaces list is still loading nothing is sent, so a user with
	// several spaces always gets the picker.
	$effect(() => {
		if (!open || $spaces.length !== 1) return;
		if (pending.length > 0) void submit();
	});

	async function submit() {
		const target = spaceId || 'default';
		const batch = pending;
		const singleTitle = items.length === 1 ? title.trim() || undefined : undefined;
		submittedOnce = true;
		for (const it of batch) {
			updateSheetItem(it.key, { submitting: true, error: undefined });
			const outcome = it.path
				? await uploadPath(it.path, target, singleTitle)
				: it.file
					? await uploadFile(it.file, target, singleTitle)
					: { name: it.name, error: 'Nothing to upload' };
			updateSheetItem(it.key, {
				submitting: false,
				documentId: outcome.document?.document_id,
				duplicate: outcome.duplicate,
				error: outcome.error,
				errorCode: outcome.errorCode
			});
		}
	}

	function close() {
		closeCorpusSheet();
		title = '';
		submittedOnce = false;
	}

	function askAboutThese() {
		const ids = submitted.map((it) => it.documentId!).filter(Boolean);
		close();
		askAboutDocuments(ids);
	}

	function openCorpus() {
		close();
		goto('/corpus');
	}

	function openDocument(documentId: string) {
		close();
		goto(`/corpus?doc=${encodeURIComponent(documentId)}`);
	}

	function tileState(it: SheetItem): { label: string; tone: string; detail?: string } {
		if (it.error) return { label: 'Failed', tone: 'text-red-400', detail: errorLabel(it.errorCode, it.error) };
		if (it.submitting) return { label: 'Sending', tone: 'text-surface-400' };
		const doc = docFor(it);
		if (!doc) return { label: it.documentId ? 'Queued' : 'Pending', tone: 'text-surface-500' };
		if (doc.status === 'failed') return { label: 'Failed', tone: 'text-red-400', detail: errorLabel(doc.error_code, doc.error) };
		if (doc.status === 'ready') {
			const parts = [doc.page_count ? `${doc.page_count} page${doc.page_count === 1 ? '' : 's'}` : null, `${doc.chunk_count} chunk${doc.chunk_count === 1 ? '' : 's'}`].filter(Boolean);
			return { label: it.duplicate ? 'Already in Corpus' : 'Ready', tone: 'text-green-400', detail: parts.join(', ') };
		}
		return { label: statusLabel(doc.status, $documentProgress[doc.document_id]), tone: 'text-laya-orange' };
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.stopPropagation();
			close();
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm"
		style="top: var(--header-h, 38px);"
		onclick={close}
		onkeydown={handleKeydown}
		transition:fade={{ duration: $reducedMotion ? 0 : 120 }}
	>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			role="dialog"
			aria-modal="true"
			aria-label="Add to Corpus"
			tabindex="-1"
			onclick={(e) => e.stopPropagation()}
			onkeydown={handleKeydown}
			class="flex max-h-[80vh] w-[520px] max-w-[92vw] flex-col rounded-xl {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-900'} shadow-2xl"
			transition:fly={{ y: 16, duration: $reducedMotion ? 0 : 180 }}
		>
			<div class="flex h-11 shrink-0 items-center justify-between border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4">
				<h3 class="text-laya-base font-semibold">Add to Corpus</h3>
				<button onclick={close} aria-label="Close" class="rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200">
					<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
				</button>
			</div>

			<div class="flex-1 space-y-3 overflow-auto p-4">
				{#if $spaces.length > 1}
					<label class="block">
						<span class="mb-1 block text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Space</span>
						<select
							bind:value={spaceId}
							disabled={submittedOnce}
							class="w-full rounded-md px-2 py-1.5 text-laya-base text-surface-200 {$glassTheme ? 'glass-input' : 'border border-surface-600 bg-surface-800'} disabled:opacity-60"
						>
							{#each $spaces as sp (sp.space_id)}
								<option value={sp.space_id}>{sp.name}</option>
							{/each}
						</select>
					</label>
				{/if}
				{#if items.length === 1 && !submittedOnce}
					<label class="block">
						<span class="mb-1 block text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Title (optional)</span>
						<input
							bind:value={title}
							placeholder={items[0].name}
							maxlength={300}
							class="w-full rounded-md px-2 py-1.5 text-laya-base text-surface-200 placeholder-surface-500 {$glassTheme ? 'glass-input' : 'border border-surface-600 bg-surface-800'}"
						/>
					</label>
				{/if}

				{#if items.length === 0}
					<p class="py-6 text-center text-laya-base text-surface-500">
						Drop files anywhere in the window to add them. Supported: PDF, DOCX, Markdown, text, CSV, JSON, HTML.
					</p>
				{:else}
					<ul class="space-y-1.5">
						{#each items as it (it.key)}
							{@const st = tileState(it)}
							{@const doc = docFor(it)}
							<li class="flex items-start gap-3 rounded-lg px-3 py-2 {$glassTheme ? 'bg-white/[0.04]' : 'bg-surface-800'}">
								<svg class="mt-0.5 h-4 w-4 shrink-0 text-surface-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6M7 4h7l5 5v11a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1z" /></svg>
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2">
										{#if doc?.status === 'ready'}
											<button onclick={() => openDocument(doc.document_id)} class="truncate text-left text-laya-base font-medium text-surface-200 hover:text-laya-orange">{doc.title}</button>
										{:else}
											<span class="truncate text-laya-base font-medium text-surface-200">{doc?.title ?? it.name}</span>
										{/if}
										<span class="ml-auto shrink-0 text-laya-micro font-medium {st.tone}">{st.label}</span>
									</div>
									{#if st.detail}
										<p class="mt-0.5 text-laya-micro text-surface-500">{st.detail}</p>
									{/if}
									{#if doc && doc.status !== 'ready' && doc.status !== 'failed'}
										{@const prog = $documentProgress[doc.document_id]}
										<div class="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-700">
											<div
												class="h-full rounded-full bg-laya-orange transition-all duration-300"
												style="width: {prog?.total ? Math.round(((prog.done ?? 0) / prog.total) * 100) : 15}%"
											></div>
										</div>
									{/if}
								</div>
								{#if !it.documentId && !it.submitting}
									<button onclick={() => removeSheetItem(it.key)} aria-label="Remove" class="shrink-0 rounded p-0.5 text-surface-500 hover:text-red-400">
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
									</button>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div class="flex shrink-0 items-center justify-end gap-2 border-t {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4 py-3">
				{#if pending.length > 0}
					<button onclick={close} class="rounded-md px-3 py-1.5 text-laya-secondary text-surface-400 hover:text-surface-200">Cancel</button>
					<button
						onclick={submit}
						class="rounded-md bg-laya-orange/80 px-3 py-1.5 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-orange"
					>
						Add {pending.length === 1 ? 'file' : `${pending.length} files`}
					</button>
				{:else}
					<button onclick={openCorpus} class="rounded-md px-3 py-1.5 text-laya-secondary text-surface-400 hover:text-surface-200">Open Corpus</button>
					<button
						onclick={askAboutThese}
						disabled={!allReady}
						title={allReady ? '' : anyFailed ? 'Some files failed' : 'Waiting for processing to finish'}
						class="rounded-md bg-laya-orange/80 px-3 py-1.5 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-orange disabled:cursor-not-allowed disabled:opacity-40"
					>
						Ask about {submitted.length === 1 ? 'this' : 'these'}
					</button>
				{/if}
			</div>
		</div>
	</div>
{/if}
