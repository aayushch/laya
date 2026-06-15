// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable, derived } from 'svelte/store';
import { engineApi } from '$lib/api/engine';
import type { WsMessage } from '$lib/api/types';

/** Outstanding failure counts surfaced in Settings → Audit:
 *  - deadEvents:      events that exhausted all retries (processing_status='dead')
 *  - ingestionErrors: uncleared n8n ingestion failures
 *
 *  Kept in sync via a one-shot startup fetch + the `audit_failure` WebSocket push
 *  (no polling). The Audit panel re-seeds these on resolve so the dot clears the
 *  moment the user retries all dead events and clears all ingestion errors. */
export const auditFailures = writable<{ deadEvents: number; ingestionErrors: number }>({
	deadEvents: 0,
	ingestionErrors: 0
});

/** True when any outstanding failure exists — drives the red dot on the Audit
 *  tab label and the Settings nav icon. */
export const hasAuditFailures = derived(
	auditFailures,
	($f) => $f.deadEvents + $f.ingestionErrors > 0
);

/** Set authoritative counts (from the startup seed, WS push, or Audit re-seed). */
export function setAuditFailureCounts(deadEvents: number, ingestionErrors: number) {
	auditFailures.set({ deadEvents, ingestionErrors });
}

/** One-shot fetch on app startup to seed the indicator. */
export async function loadAuditFailureSummary() {
	try {
		const data = await engineApi.getAuditFailureSummary();
		setAuditFailureCounts(data.dead_events, data.ingestion_errors);
	} catch {
		// Engine not ready yet — leave at zero; WS / next startup will correct.
	}
}

/** React to an `audit_failure` WebSocket message. The payload carries the
 *  authoritative current counts (not a delta), so coalesced/repeat failures
 *  keep the dot on without inflating anything. */
export function handleAuditFailureWs(msg: WsMessage) {
	const p = msg.payload as { dead_events?: number; ingestion_errors?: number };
	if (typeof p?.dead_events === 'number' && typeof p?.ingestion_errors === 'number') {
		setAuditFailureCounts(p.dead_events, p.ingestion_errors);
	}
}
