<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { lastMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ActionCard, WorkspaceSession, WorkspaceEvent } from '$lib/api/types';
	import TimelinePanel from '$lib/components/workspace/TimelinePanel.svelte';
	import AgentPanel from '$lib/components/workspace/AgentPanel.svelte';
	import ContextPanel from '$lib/components/workspace/ContextPanel.svelte';

	let card = $state<ActionCard | null>(null);
	let session = $state<WorkspaceSession | null>(null);
	let events = $state<WorkspaceEvent[]>([]);
	let context = $state<Record<string, unknown>>({});
	let loading = $state(true);
	let error = $state<string | null>(null);

	const cardId = $derived($page.params.card_id);

	onMount(async () => {
		await loadWorkspace();
	});

	async function loadWorkspace() {
		loading = true;
		error = null;
		try {
			const [cardData, workspaceData] = await Promise.all([
				engineApi.getCard(cardId),
				engineApi.getWorkspace(cardId)
			]);
			card = cardData;
			session = workspaceData.session;
			events = workspaceData.events;
			context = workspaceData.context;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load workspace';
		} finally {
			loading = false;
		}
	}

	// React to real-time WS messages for this card
	$effect(() => {
		const msg = $lastMessage;
		if (!msg || !card) return;

		const msgCardId = msg.card_id ?? (msg.payload?.card_id as string);
		const msgSessionId = msg.session_id ?? (msg.payload?.session_id as string);

		// Only process messages for this card/session
		if (msgCardId !== cardId && (!session || msgSessionId !== session.session_id)) return;

		switch (msg.type) {
			case 'card_updated': {
				const payload = msg.payload;
				if (payload.status) {
					card.status = payload.status as ActionCard['status'];
				}
				break;
			}

			case 'agent_progress':
			case 'workspace_event': {
				const newEvent: WorkspaceEvent = {
					event_id: msg.event_id ?? `ws_${Date.now()}`,
					timestamp: new Date().toISOString(),
					event_type: (msg.payload.event_type as string) ?? msg.type,
					actor: (msg.payload.actor as string) ?? 'agent',
					content: msg.payload.content as Record<string, unknown> ?? msg.payload,
					requires_input: (msg.payload.requires_input as boolean) ?? false
				};
				events = [...events, newEvent];
				break;
			}

			case 'approval_request': {
				const newEvent: WorkspaceEvent = {
					event_id: msg.event_id ?? `ws_${Date.now()}`,
					timestamp: new Date().toISOString(),
					event_type: 'approval_request',
					actor: 'agent',
					content: msg.payload as Record<string, unknown>,
					requires_input: true
				};
				events = [...events, newEvent];
				break;
			}

			case 'session_status': {
				if (session && msg.payload.status) {
					session = { ...session, status: msg.payload.status as string };
				}
				break;
			}
		}
	});

	function handleTimelineSelect(event: WorkspaceEvent) {
		// Could scroll-to in agent panel; for now, just a visual indicator
		const el = document.getElementById(`event-${event.event_id}`);
		el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}
</script>

{#if loading}
	<div class="flex h-full items-center justify-center">
		<p class="text-sm text-surface-400">Loading workspace...</p>
	</div>
{:else if error}
	<div class="flex h-full flex-col items-center justify-center gap-3">
		<p class="text-sm text-red-400">{error}</p>
		<button
			class="rounded-lg bg-surface-700 px-4 py-2 text-sm text-surface-200 hover:bg-surface-600"
			onclick={loadWorkspace}
		>Retry</button>
	</div>
{:else if card}
	<div class="flex h-[calc(100vh-64px)] -m-6">
		<TimelinePanel {events} onselect={handleTimelineSelect} />
		<AgentPanel {card} {session} {events} />
		<ContextPanel {card} {context} />
	</div>
{/if}
