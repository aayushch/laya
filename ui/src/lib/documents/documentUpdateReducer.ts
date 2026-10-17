// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Pure reducers for the Corpus `document_updated` / `document_deleted`
 * WebSocket messages, applied to the documents store's list. Kept free of
 * side effects so every transition can be unit-tested (same approach as
 * `lib/feed/cardUpdateReducer.ts`).
 */

import type { CorpusDocument, DocumentProgress, DocumentStatus } from '$lib/api/types';

export interface DocumentUpdatedMessage {
	type: 'document_updated';
	document_id: string;
	space_id?: string;
	status?: DocumentStatus;
	title?: string;
	progress?: DocumentProgress | null;
	error?: string | null;
	error_code?: string | null;
}

export interface DocumentDeletedMessage {
	type: 'document_deleted';
	document_id: string;
	space_id?: string;
}

/** Per-document ingest progress, keyed by document id. */
export type ProgressMap = Record<string, DocumentProgress>;

export interface DocumentsState {
	documents: CorpusDocument[];
	progress: ProgressMap;
}

/** Stages that end the ingest run; progress is dropped once reached. */
const TERMINAL: ReadonlySet<string> = new Set(['ready', 'failed']);

/**
 * Apply a `document_updated` message. A known document gets its status,
 * title and error fields patched in place; an unknown one is left for the
 * next list load (the message carries too little to build a full row).
 * Returns `{ state, refetch }` where `refetch` names a document whose full
 * row should be loaded: a terminal transition (counts, summary and page
 * count changed) or an unknown id.
 */
export function reduceDocumentUpdated(
	state: DocumentsState,
	msg: DocumentUpdatedMessage
): { state: DocumentsState; refetch: string | null } {
	const idx = state.documents.findIndex((d) => d.document_id === msg.document_id);
	const terminal = !!msg.status && TERMINAL.has(msg.status);

	const progress: ProgressMap = { ...state.progress };
	if (terminal || !msg.progress) {
		delete progress[msg.document_id];
	} else {
		progress[msg.document_id] = msg.progress;
	}

	if (idx < 0) {
		return { state: { ...state, progress }, refetch: msg.document_id };
	}
	const current = state.documents[idx];
	const patched: CorpusDocument = {
		...current,
		...(msg.status ? { status: msg.status } : {}),
		...(msg.title ? { title: msg.title } : {}),
		...(msg.space_id ? { space_id: msg.space_id } : {}),
		error: msg.error ?? null,
		error_code: msg.error_code ?? null
	};
	const documents = [...state.documents];
	documents[idx] = patched;
	return { state: { documents, progress }, refetch: terminal ? msg.document_id : null };
}

/** Apply a `document_deleted` message: drop the row and its progress. */
export function reduceDocumentDeleted(state: DocumentsState, msg: DocumentDeletedMessage): DocumentsState {
	const progress = { ...state.progress };
	delete progress[msg.document_id];
	return {
		documents: state.documents.filter((d) => d.document_id !== msg.document_id),
		progress
	};
}

/** Insert or replace a full document row (from a list or get response). */
export function upsertDocument(state: DocumentsState, doc: CorpusDocument): DocumentsState {
	const idx = state.documents.findIndex((d) => d.document_id === doc.document_id);
	const documents = [...state.documents];
	if (idx >= 0) documents[idx] = doc;
	else documents.unshift(doc);
	const progress = { ...state.progress };
	if (TERMINAL.has(doc.status)) delete progress[doc.document_id];
	return { documents, progress };
}

/** Human label for a status, with batch progress when embedding. */
export function statusLabel(status: DocumentStatus, progress?: DocumentProgress): string {
	switch (status) {
		case 'queued':
			return 'Queued';
		case 'parsing':
			return 'Parsing';
		case 'chunking':
			return 'Chunking';
		case 'embedding':
			if (progress?.total) return `Embedding ${progress.done ?? 0}/${progress.total}`;
			return 'Embedding';
		case 'linking':
			return 'Linking';
		case 'summarizing':
			return 'Summarising';
		case 'ready':
			return 'Ready';
		case 'failed':
			return 'Failed';
		default:
			return status;
	}
}

/** User-facing explanation for an ingest error code. */
export function errorLabel(code: string | null | undefined, fallback?: string | null): string {
	switch (code) {
		case 'no_text':
			return 'No extractable text (scanned PDF; OCR is not supported)';
		case 'encrypted':
			return 'Password-protected file';
		case 'unsupported':
			return 'Unsupported file type';
		case 'too_large':
			return 'File is too large';
		case 'embed_error':
			return 'Embedding failed; reprocess to retry';
		case 'summary_error':
			return 'Summary failed; the document is still searchable';
		case 'summary_skipped_budget':
			return 'Summary skipped: budget paused';
		case 'interrupted':
			return 'Processing was interrupted';
		case 'source_missing':
			return 'Source file moved or missing';
		case 'parse_error':
			return fallback || 'Could not parse the file';
		default:
			return fallback || (code ?? 'Unknown error');
	}
}
