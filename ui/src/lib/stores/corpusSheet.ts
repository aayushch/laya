// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// State for the "Add to Corpus" sheet: the files or paths the user dropped or
// picked, one tile each, and whether the sheet is open. Opened from the
// app-wide drop handler in the root layout, from the Corpus page and from the
// chat composer.

import { writable } from 'svelte/store';

export interface SheetItem {
	key: string;
	name: string;
	/** Browser drop or picker: the blob to upload (engine keeps a copy). */
	file?: File;
	/** Tauri drop or native picker: the path the engine reads in place. */
	path?: string;
	/** Set once the engine accepted the item. */
	documentId?: string;
	duplicate?: boolean;
	error?: string;
	errorCode?: string;
	submitting?: boolean;
}

export interface CorpusSheetState {
	open: boolean;
	items: SheetItem[];
}

export const corpusSheet = writable<CorpusSheetState>({ open: false, items: [] });

let seq = 0;

function nameOf(path: string): string {
	return path.split(/[\\/]/).pop() || path;
}

export function openCorpusSheetWithFiles(files: File[]): void {
	corpusSheet.update((s) => ({
		open: true,
		items: [...s.items, ...files.map((file) => ({ key: `f${++seq}`, name: file.name, file }))]
	}));
}

export function openCorpusSheetWithPaths(paths: string[]): void {
	corpusSheet.update((s) => ({
		open: true,
		items: [...s.items, ...paths.map((path) => ({ key: `p${++seq}`, name: nameOf(path), path }))]
	}));
}

/** Open the sheet with no files, for the picker and the empty state. */
export function openCorpusSheet(): void {
	corpusSheet.update((s) => ({ ...s, open: true }));
}

export function closeCorpusSheet(): void {
	corpusSheet.set({ open: false, items: [] });
}

export function updateSheetItem(key: string, patch: Partial<SheetItem>): void {
	corpusSheet.update((s) => ({
		...s,
		items: s.items.map((it) => (it.key === key ? { ...it, ...patch } : it))
	}));
}

export function removeSheetItem(key: string): void {
	corpusSheet.update((s) => ({ ...s, items: s.items.filter((it) => it.key !== key) }));
}
