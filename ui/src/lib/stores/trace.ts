import { writable } from 'svelte/store';
import type { TraceResponse, TraceListItem } from '$lib/api/types';

/** The currently loaded trace result */
export const currentTrace = writable<TraceResponse | null>(null);

/** Whether a narrative is currently being streamed */
export const traceNarrativeStreaming = writable<boolean>(false);

/** Accumulated narrative text from streaming */
export const traceNarrative = writable<string>('');

/** Trace history list */
export const traceHistory = writable<TraceListItem[]>([]);

/** Whether a trace search is in progress */
export const traceLoading = writable<boolean>(false);

/** Whether new events were detected for the current trace */
export const traceNewEventsDetected = writable<boolean>(false);
