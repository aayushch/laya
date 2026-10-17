// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Shared "share a file with the Corpus" calls. Inside Tauri a drop or picker
// yields a path and the engine reads the file in place; in a browser it yields
// a File blob that is uploaded (the engine keeps a copy). A duplicate (409)
// resolves to the existing document so callers can treat it as success.

import { engineApi } from '$lib/api/engine';
import type { CorpusDocument } from '$lib/api/types';
import { addDocument } from '$lib/stores/documents';

export interface UploadOutcome {
	name: string;
	document?: CorpusDocument;
	/** Set when the engine already held this file; `document` is the existing row. */
	duplicate?: boolean;
	error?: string;
	errorCode?: string;
}

interface EngineDetail {
	message?: string;
	error_code?: string;
	document_id?: string;
}

/** The engine's error detail is JSON-stringified into the Error message by `request`. */
function parseDetail(err: unknown): EngineDetail | null {
	if (!(err instanceof Error)) return null;
	try {
		const parsed = JSON.parse(err.message);
		return typeof parsed === 'object' && parsed ? (parsed as EngineDetail) : null;
	} catch {
		return null;
	}
}

async function outcomeFromError(name: string, err: unknown): Promise<UploadOutcome> {
	const detail = parseDetail(err);
	if (detail?.error_code === 'duplicate' && detail.document_id) {
		try {
			const existing = await engineApi.getDocument(detail.document_id);
			addDocument(existing);
			return { name, document: existing, duplicate: true };
		} catch {
			// fall through to the generic error
		}
	}
	const message = detail?.message ?? (err instanceof Error ? err.message : 'Upload failed');
	return { name, error: message, errorCode: detail?.error_code };
}

export async function uploadFile(file: File, spaceId: string, title?: string): Promise<UploadOutcome> {
	try {
		const doc = await engineApi.uploadDocument(file, spaceId, title);
		addDocument(doc);
		return { name: file.name, document: doc };
	} catch (err) {
		return outcomeFromError(file.name, err);
	}
}

export async function uploadPath(path: string, spaceId: string, title?: string): Promise<UploadOutcome> {
	const name = path.split(/[\\/]/).pop() || path;
	try {
		const doc = await engineApi.uploadDocumentPath(path, spaceId, title);
		addDocument(doc);
		return { name, document: doc };
	} catch (err) {
		return outcomeFromError(name, err);
	}
}
