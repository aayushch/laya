// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/** Readable label for a chat tool name: `search_cards` -> `Search cards`. */
export function humanizeToolName(name: string): string {
	const words = name.split('_').filter(Boolean).join(' ');
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}

/** Labels for the tools behind a chat message: each tool once, in first-call order. */
export function toolLabels(names: readonly string[] | undefined): string[] {
	return [...new Set(names ?? [])].map(humanizeToolName);
}
