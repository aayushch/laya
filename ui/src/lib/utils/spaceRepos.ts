// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Names a space has assigned that no configured repo carries, in their
 * assigned order. Assignments are keyed by repo name, and such a name still
 * counts for the space's repo scoping although no repo row shows it.
 */
export function unresolvedRepoNames(assigned: string[], allRepos: { name: string }[]): string[] {
	const known = new Set(allRepos.map((r) => r.name));
	return assigned.filter((name) => !known.has(name));
}
