<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import { lastMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ActionCard, WorkspaceSession, WorkspaceEvent, Repo } from '$lib/api/types';
	import TimelinePanel from '$lib/components/workspace/TimelinePanel.svelte';
	import AgentPanel from '$lib/components/workspace/AgentPanel.svelte';
	import ContextPanel from '$lib/components/workspace/ContextPanel.svelte';

	let card = $state<ActionCard | null>(null);
	let session = $state<WorkspaceSession | null>(null);
	let events = $state<WorkspaceEvent[]>([]);
	let context = $state<Record<string, unknown>>({});
	let loading = $state(true);
	let error = $state<string | null>(null);
	let timelineOpen = $state(false);

	// Shared Add Path state — lives here so both panels can access it
	let selectedAddDirs = $state<Set<string>>(new Set());
	let allRepos = $state<Repo[]>([]);

	const cardId = $derived($page.params.card_id ?? '');

	const POLL_INTERVAL_MS = 5000;
	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let isPolling = $state(false);

	onMount(async () => {
		await loadWorkspace();
		startPoller();
		try {
			const config = await engineApi.getRepos();
			allRepos = config.repos ?? [];
		} catch { /* repos unavailable */ }
	});

	onDestroy(() => {
		stopPoller();
	});

	function startPoller() {
		stopPoller();
		pollTimer = setInterval(() => pollWorkspace(), POLL_INTERVAL_MS);
	}

	function stopPoller() {
		if (pollTimer != null) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	async function pollWorkspace() {
		if (isPolling) return;
		isPolling = true;
		try {
			const workspaceData = await engineApi.getWorkspace(cardId);
			session = workspaceData.session;
			events = workspaceData.events;
			context = workspaceData.context;
			// Also refresh card status
			if (card) {
				const cardData = await engineApi.getCard(cardId);
				card = cardData;
			}
		} catch {
			// Silently ignore poll errors — next poll will retry
		} finally {
			isPolling = false;
		}
	}

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

	// Track last processed message to avoid re-processing the same one
	// when loadWorkspace() updates card/session and re-triggers the $effect.
	let lastProcessedMsg: unknown = null;

	// React to real-time WS messages for this card.
	// Only approval_request, agent_error, and agent_completed are broadcast;
	// all other events are fetched from the DB via loadWorkspace().
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;
		// Dedupe: skip if we already processed this exact message object
		if (msg === lastProcessedMsg) return;

		const msgCardId = msg.card_id ?? (msg.payload?.card_id as string);
		const msgSessionId = msg.session_id ?? (msg.payload?.session_id as string);

		// Only process messages for this card/session
		if (msgCardId !== cardId && (!session || msgSessionId !== session?.session_id)) return;

		lastProcessedMsg = msg;

		switch (msg.type) {
			case 'card_updated': {
				const payload = msg.payload;
				if (payload.status) {
					if (card) card.status = payload.status as ActionCard['status'];
					// Agent just started — refresh to get session info.
					// pollWorkspace (not loadWorkspace) so we don't flip the
					// route-level `loading` flag and unmount the whole panel.
					if (payload.status === 'agent_running') pollWorkspace();
				}
				break;
			}

			case 'approval_request': {
				pollWorkspace();
				break;
			}

			case 'agent_error': {
				pollWorkspace();
				break;
			}

			case 'agent_completed': {
				pollWorkspace();
				break;
			}

			case 'card_deleted': {
				goto('/feed');
				break;
			}
		}
	});

	function handleTimelineSelect(event: WorkspaceEvent) {
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
	<!-- -m-4 cancels main's p-4; h-[calc(100%+2rem)] reclaims the vertical padding so
		 the workspace fills the viewport exactly and only individual panels scroll. -->
	<div class="flex h-[calc(100%+2rem)] -m-4 overflow-hidden">
		<!-- Collapsible timeline panel -->
		<div class="shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out {timelineOpen ? 'w-72' : 'w-0'}">
			<div class="h-full w-72">
				<TimelinePanel {events} onselect={handleTimelineSelect} />
			</div>
		</div>
		<AgentPanel {card} {session} {events} {timelineOpen} {selectedAddDirs} ontoggletime={() => (timelineOpen = !timelineOpen)} />
		<ContextPanel {card} {session} {events} {context} {allRepos} bind:selectedAddDirs />
	</div>
{/if}
