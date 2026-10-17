// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Corpus documents store. Holds every document the engine knows about (all
// spaces) plus per-document ingest progress fed by the document_updated
// WebSocket message. Initialised once from the root layout so progress keeps
// flowing while the Corpus page and the add-document sheet are closed.

import { derived, get, writable } from 'svelte/store';
import type { CorpusDocument } from '$lib/api/types';
import { engineApi } from '$lib/api/engine';
import { lastMessage } from './websocket';
import {
	reduceDocumentDeleted,
	reduceDocumentUpdated,
	upsertDocument,
	type DocumentDeletedMessage,
	type DocumentsState,
	type DocumentUpdatedMessage
} from '$lib/documents/documentUpdateReducer';

export const documentsState = writable<DocumentsState>({ documents: [], progress: {} });

/** All documents, newest first (the engine's list order). */
export const documents = derived(documentsState, ($s) => $s.documents);
/** Per-document ingest progress, keyed by document id. */
export const documentProgress = derived(documentsState, ($s) => $s.progress);
/** Ready documents only, for pickers. */
export const readyDocuments = derived(documentsState, ($s) => $s.documents.filter((d) => d.status === 'ready'));

export const documentsLoaded = writable(false);

let initialized = false;

/** Load the full document list from the engine (all spaces). */
export async function loadDocuments(): Promise<void> {
	try {
		const resp = await engineApi.listDocuments({ limit: 500 });
		documentsState.update((s) => ({ documents: resp.documents, progress: s.progress }));
	} catch {
		// Engine not ready; keep what we have.
	} finally {
		documentsLoaded.set(true);
	}
}

/** Fetch one document's full row and merge it into the list. */
export async function refreshDocument(documentId: string): Promise<CorpusDocument | null> {
	try {
		const doc = await engineApi.getDocument(documentId);
		documentsState.update((s) => upsertDocument(s, doc));
		return doc;
	} catch {
		return null;
	}
}

/** Put a document row (from an upload response) into the list immediately. */
export function addDocument(doc: CorpusDocument): void {
	documentsState.update((s) => upsertDocument(s, doc));
}

export function removeDocument(documentId: string): void {
	documentsState.update((s) =>
		reduceDocumentDeleted(s, { type: 'document_deleted', document_id: documentId })
	);
}

export function documentById(documentId: string): CorpusDocument | undefined {
	return get(documentsState).documents.find((d) => d.document_id === documentId);
}

/** Subscribe to WebSocket document messages. Idempotent. */
export function initDocumentsStore(): void {
	if (initialized) return;
	initialized = true;
	lastMessage.subscribe((msg) => {
		if (!msg) return;
		if (msg.type === 'document_updated') {
			const m = msg as unknown as DocumentUpdatedMessage;
			let refetch: string | null = null;
			documentsState.update((s) => {
				const r = reduceDocumentUpdated(s, m);
				refetch = r.refetch;
				return r.state;
			});
			if (refetch) void refreshDocument(refetch);
		} else if (msg.type === 'document_deleted') {
			const m = msg as unknown as DocumentDeletedMessage;
			documentsState.update((s) => reduceDocumentDeleted(s, m));
		}
	});
}
