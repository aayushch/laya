// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Attach Corpus documents to the open chat conversation. Used by the composer's
// paperclip, by "Add file" and by a drop onto the window while a conversation
// is open: the file is shared with the Corpus (path in Tauri, upload in a
// browser) and its id is added to the conversation's attachment list.

import { get } from 'svelte/store';
import { engineApi } from '$lib/api/engine';
import { activeConversationId, chatDocumentIds } from '$lib/stores/chat';
import { feedFilters } from '$lib/stores/feedFilters';
import { uploadFile, uploadPath, type UploadOutcome } from './upload';

/** Space new files land in: the single selected feed space, else default. */
export function defaultSpaceForNewDocuments(): string {
	const sel = get(feedFilters).spaceFilter;
	return sel.length === 1 ? sel[0] : 'default';
}

/** Add ids to the attachment list and persist on the conversation if one exists. */
export function attachDocumentIds(ids: string[]): void {
	const current = get(chatDocumentIds) ?? [];
	const next = [...current];
	for (const id of ids) if (!next.includes(id)) next.push(id);
	chatDocumentIds.set(next);
	const convId = get(activeConversationId);
	if (convId) engineApi.setConversationDocuments(convId, next).catch(() => {});
}

/** Remove one id; an empty list stays `[]` so the next send detaches it on the engine. */
export function detachDocumentId(id: string): void {
	const next = (get(chatDocumentIds) ?? []).filter((d) => d !== id);
	chatDocumentIds.set(next);
	const convId = get(activeConversationId);
	if (convId) engineApi.setConversationDocuments(convId, next).catch(() => {});
}

/** Share dropped files or paths and attach whatever the engine accepted. */
export async function attachDroppedToChat(input: { files?: File[]; paths?: string[] }): Promise<UploadOutcome[]> {
	const space = defaultSpaceForNewDocuments();
	const outcomes: UploadOutcome[] = [];
	for (const path of input.paths ?? []) outcomes.push(await uploadPath(path, space));
	for (const file of input.files ?? []) outcomes.push(await uploadFile(file, space));
	const ids = outcomes.map((o) => o.document?.document_id).filter((id): id is string => !!id);
	if (ids.length) attachDocumentIds(ids);
	return outcomes;
}
