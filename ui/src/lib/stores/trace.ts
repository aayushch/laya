// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable, derived } from 'svelte/store';
import type { TraceResponse, TraceListItem } from '$lib/api/types';

/** The currently loaded trace result */
export const currentTrace = writable<TraceResponse | null>(null);

/** Per-cluster narrative streaming state: cluster_id -> boolean */
export const traceNarrativeStreamingMap = writable<Record<string, boolean>>({});

/** Per-cluster accumulated narrative text: cluster_id -> string */
export const traceNarrativeMap = writable<Record<string, string>>({});

// Legacy single-value aliases (kept for backward compat if needed)
/** @deprecated Use traceNarrativeStreamingMap */
export const traceNarrativeStreaming = writable<boolean>(false);
/** @deprecated Use traceNarrativeMap */
export const traceNarrative = writable<string>('');

/** Trace history list */
export const traceHistory = writable<TraceListItem[]>([]);

/** Whether a trace search is in progress */
export const traceLoading = writable<boolean>(false);

/** Whether new events were detected for the current trace */
export const traceNewEventsDetected = writable<boolean>(false);

/** Progress state during trace search */
export const traceProgress = writable<{ stage: string; step: number; total: number; query: string } | null>(null);
