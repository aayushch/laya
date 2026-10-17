// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import type { CorpusDocument } from '$lib/api/types';
import {
	errorLabel,
	reduceDocumentDeleted,
	reduceDocumentUpdated,
	statusLabel,
	upsertDocument,
	type DocumentsState
} from './documentUpdateReducer';

function doc(overrides: Partial<CorpusDocument> = {}): CorpusDocument {
	return {
		document_id: 'doc_a',
		space_id: 'default',
		title: 'A',
		original_filename: 'a.pdf',
		file_type: 'pdf',
		mime_type: null,
		size_bytes: 1,
		page_count: null,
		chunk_count: 0,
		char_count: 0,
		storage_mode: 'reference',
		source_path: '/tmp/a.pdf',
		source_state: 'ok',
		status: 'queued',
		error: null,
		error_code: null,
		summary: null,
		summary_model: null,
		embedding_model: null,
		needs_reembed: false,
		created_at: '2026-10-17 00:00:00',
		updated_at: '2026-10-17 00:00:00',
		indexed_at: null,
		entity_links: [],
		...overrides
	};
}

describe('reduceDocumentUpdated', () => {
	it('patches status and tracks progress for a known document', () => {
		const state: DocumentsState = { documents: [doc()], progress: {} };
		const { state: next, refetch } = reduceDocumentUpdated(state, {
			type: 'document_updated',
			document_id: 'doc_a',
			status: 'embedding',
			progress: { stage: 'embedding', done: 3, total: 10 }
		});
		expect(next.documents[0].status).toBe('embedding');
		expect(next.progress.doc_a).toEqual({ stage: 'embedding', done: 3, total: 10 });
		expect(refetch).toBeNull();
		expect(state.documents[0].status).toBe('queued'); // input untouched
	});

	it('drops progress and asks for a refetch on a terminal status', () => {
		const state: DocumentsState = {
			documents: [doc({ status: 'embedding' })],
			progress: { doc_a: { stage: 'embedding', done: 9, total: 10 } }
		};
		const { state: next, refetch } = reduceDocumentUpdated(state, {
			type: 'document_updated',
			document_id: 'doc_a',
			status: 'ready',
			progress: null
		});
		expect(next.documents[0].status).toBe('ready');
		expect(next.progress.doc_a).toBeUndefined();
		expect(refetch).toBe('doc_a');
	});

	it('records errors and clears them on the next message', () => {
		let state: DocumentsState = { documents: [doc()], progress: {} };
		state = reduceDocumentUpdated(state, {
			type: 'document_updated',
			document_id: 'doc_a',
			status: 'failed',
			error: 'scanned',
			error_code: 'no_text'
		}).state;
		expect(state.documents[0].error_code).toBe('no_text');
		state = reduceDocumentUpdated(state, { type: 'document_updated', document_id: 'doc_a', status: 'queued' }).state;
		expect(state.documents[0].error_code).toBeNull();
	});

	it('asks for a refetch when the document is unknown', () => {
		const { state, refetch } = reduceDocumentUpdated(
			{ documents: [], progress: {} },
			{ type: 'document_updated', document_id: 'doc_new', status: 'parsing', progress: { stage: 'parsing' } }
		);
		expect(state.documents).toEqual([]);
		expect(state.progress.doc_new).toEqual({ stage: 'parsing' });
		expect(refetch).toBe('doc_new');
	});
});

describe('reduceDocumentDeleted and upsertDocument', () => {
	it('removes the row and its progress', () => {
		const state: DocumentsState = {
			documents: [doc(), doc({ document_id: 'doc_b' })],
			progress: { doc_a: { stage: 'embedding' } }
		};
		const next = reduceDocumentDeleted(state, { type: 'document_deleted', document_id: 'doc_a' });
		expect(next.documents.map((d) => d.document_id)).toEqual(['doc_b']);
		expect(next.progress).toEqual({});
	});

	it('upsert replaces in place or prepends', () => {
		let state: DocumentsState = { documents: [doc({ document_id: 'doc_b' })], progress: { doc_a: { stage: 'parsing' } } };
		state = upsertDocument(state, doc({ status: 'ready' }));
		expect(state.documents.map((d) => d.document_id)).toEqual(['doc_a', 'doc_b']);
		expect(state.progress.doc_a).toBeUndefined();
		state = upsertDocument(state, doc({ document_id: 'doc_b', title: 'B2' }));
		expect(state.documents[1].title).toBe('B2');
	});
});

describe('labels', () => {
	it('statusLabel shows embedding progress', () => {
		expect(statusLabel('embedding', { stage: 'embedding', done: 2, total: 5 })).toBe('Embedding 2/5');
		expect(statusLabel('ready')).toBe('Ready');
	});

	it('errorLabel explains codes and falls back to the message', () => {
		expect(errorLabel('no_text')).toContain('scanned PDF');
		expect(errorLabel('parse_error', 'bad xref')).toBe('bad xref');
		expect(errorLabel(null, null)).toBe('Unknown error');
	});
});
