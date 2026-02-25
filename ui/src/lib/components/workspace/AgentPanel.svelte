<script lang="ts">
	import type { ActionCard, WorkspaceEvent, WorkspaceSession } from '$lib/api/types';
	import { sendMessage } from '$lib/stores/websocket';

	let {
		card,
		session,
		events
	}: {
		card: ActionCard;
		session: WorkspaceSession | null;
		events: WorkspaceEvent[];
	} = $props();

	let userInput = $state('');
	let approvalReason = $state('');
	let showDenyInput = $state<string | null>(null);

	const agentEvents = $derived(
		events.filter((e) =>
			['agent_message', 'approval_request', 'user_response', 'error', 'file_write'].includes(
				e.event_type
			)
		)
	);

	const sessionStatusColors: Record<string, string> = {
		starting: 'bg-cyan-900/50 text-cyan-300',
		running: 'bg-blue-900/50 text-blue-300',
		awaiting_input: 'bg-yellow-900/50 text-yellow-300',
		paused: 'bg-surface-700 text-surface-300',
		completed: 'bg-green-900/50 text-green-300',
		failed: 'bg-red-900/50 text-red-300',
		cancelled: 'bg-surface-700 text-surface-400'
	};

	function sendUserInput() {
		if (!userInput.trim() || !session) return;
		sendMessage({
			type: 'user_input',
			session_id: session.session_id,
			payload: { message: userInput.trim() }
		});
		userInput = '';
	}

	function approveAction(eventId: string) {
		if (!session) return;
		sendMessage({
			type: 'approve_action',
			session_id: session.session_id,
			payload: { event_id: eventId }
		});
		showDenyInput = null;
	}

	function denyAction(eventId: string) {
		if (!session) return;
		sendMessage({
			type: 'deny_action',
			session_id: session.session_id,
			payload: { reason: approvalReason || 'no', event_id: eventId }
		});
		approvalReason = '';
		showDenyInput = null;
	}

	function controlSession(action: string) {
		if (!session) return;
		sendMessage({
			type: 'session_control',
			session_id: session.session_id,
			payload: { action }
		});
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			sendUserInput();
		}
	}
</script>

<div class="flex h-full flex-1 flex-col">
	<!-- Header bar -->
	<div class="flex items-center justify-between border-b border-surface-700 px-4 py-3">
		<div class="flex items-center gap-2">
			<h2 class="text-sm font-semibold text-surface-100 truncate max-w-[300px]">{card.header}</h2>
			{#if session}
				<span class="rounded px-1.5 py-0.5 text-[10px] font-medium {sessionStatusColors[session.status] ?? 'bg-surface-700 text-surface-300'}">
					{session.status}
				</span>
				<span class="text-[10px] text-surface-500">{session.agent_type}</span>
			{/if}
		</div>

		{#if session && !['completed', 'failed', 'cancelled'].includes(session.status)}
			<div class="flex gap-1">
				{#if session.status === 'paused'}
					<button
						class="rounded px-2 py-1 text-[11px] text-surface-300 hover:bg-surface-700"
						onclick={() => controlSession('resume')}
					>Resume</button>
				{:else}
					<button
						class="rounded px-2 py-1 text-[11px] text-surface-300 hover:bg-surface-700"
						onclick={() => controlSession('pause')}
					>Pause</button>
				{/if}
				<button
					class="rounded px-2 py-1 text-[11px] text-red-400 hover:bg-red-900/30"
					onclick={() => controlSession('cancel')}
				>Cancel</button>
			</div>
		{/if}
	</div>

	<!-- Event stream -->
	<div class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
		{#each agentEvents as event}
			{@const isUser = event.actor === 'user'}
			<div class="flex {isUser ? 'justify-end' : 'justify-start'}">
				<div class="max-w-[80%] rounded-lg px-3 py-2 {isUser ? 'bg-blue-900/30 text-blue-100' : event.event_type === 'error' ? 'bg-red-900/20 text-red-200' : 'bg-surface-800 text-surface-200'}">
					{#if event.event_type === 'approval_request'}
						<div class="mb-2 text-xs text-yellow-300 font-medium">Approval Required</div>
						<p class="text-xs whitespace-pre-wrap">{event.content.description ?? event.content.message ?? JSON.stringify(event.content)}</p>
						{#if showDenyInput !== event.event_id}
							<div class="mt-2 flex gap-2">
								<button
									class="rounded bg-green-700/40 px-3 py-1 text-xs text-green-300 hover:bg-green-700/60"
									onclick={() => approveAction(event.event_id)}
								>Approve</button>
								<button
									class="rounded bg-surface-700/50 px-3 py-1 text-xs text-surface-300 hover:bg-surface-600"
									onclick={() => (showDenyInput = event.event_id)}
								>Deny</button>
							</div>
						{:else}
							<div class="mt-2 flex gap-2">
								<input
									bind:value={approvalReason}
									placeholder="Reason (optional)"
									class="flex-1 rounded border border-surface-600 bg-surface-900 px-2 py-1 text-xs text-surface-100 placeholder-surface-500"
								/>
								<button
									class="rounded bg-red-700/40 px-3 py-1 text-xs text-red-300 hover:bg-red-700/60"
									onclick={() => denyAction(event.event_id)}
								>Deny</button>
								<button
									class="text-xs text-surface-400 hover:text-surface-200"
									onclick={() => (showDenyInput = null)}
								>Cancel</button>
							</div>
						{/if}
					{:else if event.event_type === 'file_write'}
						<div class="text-[10px] text-violet-400 mb-1">File Write</div>
						<p class="text-xs font-mono text-violet-300">{event.content.file_path ?? event.content.path ?? ''}</p>
					{:else}
						<p class="text-xs whitespace-pre-wrap">{event.content.message ?? event.content.text ?? JSON.stringify(event.content)}</p>
					{/if}
					<p class="mt-1 text-[10px] text-surface-500">
						{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
					</p>
				</div>
			</div>
		{/each}

		{#if agentEvents.length === 0}
			<div class="py-12 text-center text-sm text-surface-500">
				{#if session}
					Waiting for agent output...
				{:else}
					No workspace session
				{/if}
			</div>
		{/if}
	</div>

	<!-- Input bar -->
	{#if session && !['completed', 'failed', 'cancelled'].includes(session.status)}
		<div class="border-t border-surface-700 px-4 py-3">
			<div class="flex gap-2">
				<textarea
					bind:value={userInput}
					placeholder="Send a message to the agent..."
					rows="1"
					class="flex-1 resize-none rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-100 placeholder-surface-500 focus:border-blue-600 focus:outline-none"
					onkeydown={handleKeydown}
				></textarea>
				<button
					class="rounded-lg bg-blue-700/40 px-4 py-2 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-700/60 disabled:opacity-50"
					onclick={sendUserInput}
					disabled={!userInput.trim()}
				>Send</button>
			</div>
		</div>
	{/if}
</div>
