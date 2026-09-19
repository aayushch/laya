// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable, derived } from 'svelte/store';
import { engineApi } from '$lib/api/engine';
import type { EgressConnection } from '$lib/api/types';

/** Count of egress connections in a non-connected state ('error' | 'expired')
 *  — e.g. failed credential validation or a dead OAuth grant.
 *
 *  Kept in sync via a one-shot startup fetch, the `connection_status`
 *  WebSocket push from the engine's health monitor, and re-seeds whenever
 *  the Integrations tab (re)loads its connection list (connect / test /
 *  disconnect all go through that load). Mirrors the auditFailures store. */
export const integrationErrors = writable(0);

/** True when any connection is unhealthy — drives the red dot on the
 *  Integrations tab label and (with audit failures) the Settings nav icon. */
export const hasIntegrationErrors = derived(integrationErrors, ($n) => $n > 0);

/** Re-seed from an already-fetched connections list (Integrations tab load). */
export function setIntegrationErrorsFromConnections(connections: EgressConnection[]) {
	integrationErrors.set(connections.filter((c) => c.status !== 'connected').length);
}

/** Fetch the authoritative count. Used for the startup seed and on
 *  `connection_status` WS pushes — the push only carries one connection's
 *  new status (not counts), so we refetch rather than track deltas. These
 *  pushes are rare (health monitor runs every 30 min), so this is cheap. */
export async function loadIntegrationErrorSummary() {
	try {
		const data = await engineApi.listEgressConnections();
		setIntegrationErrorsFromConnections(data.connections);
	} catch {
		// Engine not ready yet — leave as-is; WS / next startup will correct.
	}
}
