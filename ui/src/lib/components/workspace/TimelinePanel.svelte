<script lang="ts">
	import type { WorkspaceEvent } from '$lib/api/types';
	import { glassTheme } from '$lib/stores/glassTheme';

	let { events, onselect }: { events: WorkspaceEvent[]; onselect?: (event: WorkspaceEvent) => void } = $props();

	const VISIBLE_LIMIT = 150;
	let showAll = $state(false);

	const visibleEvents = $derived(
		showAll || events.length <= VISIBLE_LIMIT
			? events
			: events.slice(-VISIBLE_LIMIT)
	);
	const hiddenCount = $derived(events.length - visibleEvents.length);

	const typeColors: Record<string, string> = {
		agent_message: 'border-laya-orange/50',
		user_response: 'border-laya-peach/40',
		tool_call: 'border-laya-gold/40',
		file_read: 'border-laya-sand/35',
		file_write: 'border-laya-coral/45',
		approval_request: 'border-laya-amber/50',
		approval_response: 'border-laya-peach/40',
		status_change: 'border-laya-gold/35',
		error: 'border-laya-terracotta/50'
	};

	const typeIcons: Record<string, { path: string; viewBox?: string }> = {
		agent_message: { path: 'M12 2a2 2 0 0 1 2 2v1h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3l-3 3-3-3H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2V4a2 2 0 0 1 2-2h4Z' },
		user_response: { path: 'M12 12c2.7 0 5-2.3 5-5s-2.3-5-5-5-5 2.3-5 5 2.3 5 5 5Zm0 2c-3.3 0-10 1.7-10 5v2h20v-2c0-3.3-6.7-5-10-5Z' },
		tool_call: { path: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76Z' },
		file_read: { path: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm-2 1.5L18.5 10H12V3.5ZM8 13h8v1.5H8V13Zm0 3h5v1.5H8V16Z' },
		file_write: { path: 'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25ZM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z' },
		approval_request: { path: 'M1 21h22L12 2 1 21Zm12-3h-2v-2h2v2Zm0-4h-2v-4h2v4Z' },
		approval_response: { path: 'M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17Z' },
		status_change: { path: 'M12 4V1L8 5l4 4V6a6 6 0 0 1 6 6 5.87 5.87 0 0 1-.7 2.8l1.46 1.46A8 8 0 0 0 12 4ZM12 18a6 6 0 0 1-6-6c0-1 .25-1.97.7-2.8L5.24 7.74A8 8 0 0 0 12 20v3l4-4-4-4v3Z' },
		error: { path: 'M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12 19 6.41Z' }
	};

	function eventSummary(event: WorkspaceEvent): string {
		const c = event.content;
		switch (event.event_type) {
			case 'file_read':
			case 'file_write':
				return (c.file as string) ?? (c.file_path as string) ?? event.event_type;
			case 'agent_message':
				return ((c.text as string) ?? (c.message as string) ?? '').slice(0, 60) || 'Agent message';
			case 'user_response':
				return ((c.text as string) ?? (c.message as string) ?? '').slice(0, 60) || 'User message';
			case 'tool_call':
				return (c.tool as string) ?? 'Tool call';
			case 'approval_request':
				return (c.description as string)?.slice(0, 60) ?? 'Approval needed';
			case 'approval_response':
				return c.approved ? 'Approved' : 'Denied';
			case 'status_change':
				return (c.action as string) ?? (c.status as string) ?? 'Status changed';
			case 'error':
				return ((c.message as string) ?? (c.error as string) ?? 'Error').slice(0, 60);
			default:
				return event.event_type;
		}
	}

	function formatTime(ts: string): string {
		return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	}
</script>

<div class="flex h-full w-72 flex-col border-t {$glassTheme ? 'glass-panel border-white/[0.06]' : 'border-surface-700 bg-surface-850'}">
	<div class="flex h-11 items-center gap-2 border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4">
		<h2 class="text-xs font-semibold uppercase tracking-wider text-surface-400">Timeline</h2>
		<span class="text-[10px] text-surface-500">{events.length} events</span>
	</div>

	<div class="flex-1 overflow-y-auto">
		{#if hiddenCount > 0}
			<button
				class="flex w-full items-center justify-center gap-1 border-b {$glassTheme ? 'border-white/[0.06] hover:bg-white/[0.04]' : 'border-surface-700 hover:bg-surface-800'} px-3 py-2 text-[11px] text-surface-400 transition-colors hover:text-surface-200"
				onclick={() => (showAll = true)}
			>
				Show {hiddenCount} older events
			</button>
		{/if}

		{#each visibleEvents as event (event.event_id)}
			<button
				class="group flex w-full items-start gap-2 border-l-2 px-3 py-2 text-left transition-colors {$glassTheme ? 'hover:bg-white/[0.04]' : 'hover:bg-surface-800'} {typeColors[event.event_type] ?? 'border-surface-600'}"
				onclick={() => onselect?.(event)}
			>
				<svg class="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-surface-400" viewBox="0 0 24 24" fill="currentColor">
					{#if typeIcons[event.event_type]}
						<path d={typeIcons[event.event_type].path} />
					{:else}
						<circle cx="12" cy="12" r="5" />
					{/if}
				</svg>
				<div class="min-w-0 flex-1">
					<p class="truncate text-xs text-surface-200">{eventSummary(event)}</p>
					<p class="text-[10px] text-surface-500">{formatTime(event.timestamp)}</p>
				</div>
				{#if event.requires_input}
					<span class="mt-0.5 flex-shrink-0 rounded bg-yellow-900/50 px-1 py-0.5 text-[9px] font-medium text-yellow-300">INPUT</span>
				{/if}
			</button>
		{/each}

		{#if events.length === 0}
			<div class="px-4 py-8 text-center text-xs text-surface-500">No events yet</div>
		{/if}
	</div>
</div>
