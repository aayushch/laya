<script lang="ts">
	import type { WorkspaceEvent } from '$lib/api/types';

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

	const typeIcons: Record<string, string> = {
		agent_message: '\u{1F916}',
		user_response: '\u{1F464}',
		tool_call: '\u{1F527}',
		file_read: '\u{1F4C4}',
		file_write: '\u{270F}\uFE0F',
		approval_request: '\u{26A0}\uFE0F',
		approval_response: '\u{2705}',
		status_change: '\u{1F504}',
		error: '\u{274C}'
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

<div class="flex h-full w-72 flex-col border-r border-surface-700 bg-surface-850">
	<div class="flex h-11 items-center gap-2 border-b border-surface-700 px-4">
		<h2 class="text-xs font-semibold uppercase tracking-wider text-surface-400">Timeline</h2>
		<span class="text-[10px] text-surface-500">{events.length} events</span>
	</div>

	<div class="flex-1 overflow-y-auto">
		{#if hiddenCount > 0}
			<button
				class="flex w-full items-center justify-center gap-1 border-b border-surface-700 px-3 py-2 text-[11px] text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200"
				onclick={() => (showAll = true)}
			>
				Show {hiddenCount} older events
			</button>
		{/if}

		{#each visibleEvents as event (event.event_id)}
			<button
				class="group flex w-full items-start gap-2 border-l-2 px-3 py-2 text-left transition-colors hover:bg-surface-800 {typeColors[event.event_type] ?? 'border-surface-600'}"
				onclick={() => onselect?.(event)}
			>
				<span class="mt-0.5 flex-shrink-0 text-xs">{typeIcons[event.event_type] ?? '\u{25CF}'}</span>
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
