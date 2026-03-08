<script lang="ts">
	import type { ActionCard, WorkspaceEvent, WorkspaceSession, Repo } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { sendMessage } from '$lib/stores/websocket';
	import { tick, onMount } from 'svelte';
	import { marked } from 'marked';

	let {
		card,
		session,
		events
	}: {
		card: ActionCard;
		session: WorkspaceSession | null;
		events: WorkspaceEvent[];
	} = $props();

	const VISIBLE_LIMIT = 150;
	let showAll = $state(false);

	let userInput = $state('');
	let approvalReason = $state('');
	let showDenyInput = $state<string | null>(null);
	let scrollContainer = $state<HTMLElement | null>(null);
	let sendingPrompt = $state(false);

	// AskUserQuestion state
	let questionSelections = $state<Record<string, string>>({});
	let submittingAnswer = $state(false);

	// Add Path state
	let allRepos = $state<Repo[]>([]);
	let selectedAddDirs = $state<Set<string>>(new Set());
	let showAddPath = $state(false);

	onMount(async () => {
		try {
			const config = await engineApi.getRepos();
			allRepos = config.repos ?? [];
		} catch { /* repos unavailable */ }
	});

	// Repos available to add (exclude the session's current cwd)
	const availableRepos = $derived(
		allRepos.filter((r) => r.path !== session?.repo_path)
	);

	function toggleDir(path: string) {
		const next = new Set(selectedAddDirs);
		if (next.has(path)) next.delete(path);
		else next.add(path);
		selectedAddDirs = next;
	}

	const addDirsArray = $derived(
		selectedAddDirs.size > 0 ? [...selectedAddDirs] : undefined
	);

	const AGENT_EVENT_TYPES = new Set([
		'agent_message',
		'approval_request',
		'user_response',
		'error',
		'file_write',
		'tool_call',
		'file_read'
	]);

	const TOOL_EVENT_TYPES = new Set(['tool_call', 'file_read', 'file_write']);

	// Agent is actively working (not waiting for user)
	const isAgentActive = $derived(
		session != null && ['starting', 'running'].includes(session.status)
	);

	// Session is in a terminal state
	const isSessionDone = $derived(
		session != null && ['completed', 'failed', 'cancelled'].includes(session.status)
	);

	/** Check if an event carries a plan (new is_plan format or legacy ExitPlanMode tool_call). */
	function isPlanEvent(ev: WorkspaceEvent): boolean {
		if (ev.content.is_plan) return true;
		if (ev.event_type === 'tool_call' && ev.content.tool === 'ExitPlanMode') return true;
		return false;
	}

	/** Extract plan markdown from either format. */
	function getPlanText(ev: WorkspaceEvent): string {
		if (ev.content.is_plan) return String(ev.content.text ?? '');
		// Legacy: plan is nested inside content.input
		const input = ev.content.input as Record<string, unknown> | undefined;
		return String(input?.plan ?? '');
	}

	const agentEvents = $derived(
		events.filter((e) => AGENT_EVENT_TYPES.has(e.event_type))
	);

	const visibleAgentEvents = $derived(
		showAll || agentEvents.length <= VISIBLE_LIMIT
			? agentEvents
			: agentEvents.slice(-VISIBLE_LIMIT)
	);
	const hiddenCount = $derived(agentEvents.length - visibleAgentEvents.length);

	// Check if any question event has already been answered (a user_response follows it)
	const answeredQuestionIds = $derived.by(() => {
		const ids = new Set<string>();
		for (let i = 0; i < events.length; i++) {
			const ev = events[i];
			if (ev.event_type === 'approval_request' && ev.content.ask_user_question) {
				// Check if a user_response follows anywhere after
				for (let j = i + 1; j < events.length; j++) {
					if (events[j].event_type === 'user_response') {
						ids.add(ev.event_id);
						break;
					}
				}
			}
		}
		return ids;
	});

	// Group consecutive tool events into collapsible blocks
	type Segment =
		| { kind: 'event'; event: WorkspaceEvent }
		| { kind: 'tool_group'; events: WorkspaceEvent[] };

	const segments = $derived.by(() => {
		const result: Segment[] = [];
		let toolBuffer: WorkspaceEvent[] = [];

		function flushTools() {
			if (toolBuffer.length > 0) {
				result.push({ kind: 'tool_group', events: [...toolBuffer] });
				toolBuffer = [];
			}
		}

		for (const ev of visibleAgentEvents) {
			if (TOOL_EVENT_TYPES.has(ev.event_type) && !isPlanEvent(ev)) {
				toolBuffer.push(ev);
			} else {
				flushTools();
				result.push({ kind: 'event', event: ev });
			}
		}
		flushTools();
		return result;
	});

	// Track expanded tool groups by index
	let expandedGroups = $state<Set<number>>(new Set());

	function toggleGroup(idx: number) {
		const next = new Set(expandedGroups);
		if (next.has(idx)) next.delete(idx);
		else next.add(idx);
		expandedGroups = next;
	}

	// Auto-scroll to bottom when new events arrive
	let prevEventCount = $state(0);
	$effect(() => {
		const count = visibleAgentEvents.length;
		if (count > prevEventCount && scrollContainer) {
			tick().then(() => {
				if (scrollContainer) {
					scrollContainer.scrollTop = scrollContainer.scrollHeight;
				}
			});
		}
		prevEventCount = count;
	});

	const sessionStatusColors: Record<string, string> = {
		starting: 'bg-cyan-900/50 text-cyan-300',
		running: 'bg-blue-900/50 text-blue-300',
		awaiting_input: 'bg-yellow-900/50 text-yellow-300',
		paused: 'bg-surface-700 text-surface-300',
		completed: 'bg-green-900/50 text-green-300',
		failed: 'bg-red-900/50 text-red-300',
		cancelled: 'bg-surface-700 text-surface-400'
	};

	const toolIcons: Record<string, string> = {
		file_read: '\u{1F4C4}',
		file_write: '\u{270F}\uFE0F',
		tool_call: '\u{1F527}'
	};

	function toolLabel(ev: WorkspaceEvent): string {
		const c = ev.content;
		const tool = (c.tool as string) ?? '';
		const file = (c.file as string) ?? '';
		if (ev.event_type === 'file_read' || ev.event_type === 'file_write') {
			return file || tool;
		}
		if (tool === 'Bash') {
			const input = c.input as Record<string, unknown> | undefined;
			const cmd = (input?.command as string) ?? '';
			const desc = (input?.description as string) ?? '';
			return desc || (cmd.length > 60 ? cmd.slice(0, 57) + '...' : cmd) || 'Bash';
		}
		if (tool === 'Grep' || tool === 'Glob') {
			const input = c.input as Record<string, unknown> | undefined;
			const pattern = (input?.pattern as string) ?? '';
			return `${tool}: ${pattern.length > 50 ? pattern.slice(0, 47) + '...' : pattern}`;
		}
		return tool || 'Tool';
	}

	function selectOption(eventId: string, qIdx: number, label: string) {
		const key = `${eventId}_${qIdx}`;
		questionSelections = { ...questionSelections, [key]: label };
	}

	async function submitAnswers(event: WorkspaceEvent) {
		if (!session) return;
		submittingAnswer = true;
		try {
			const questions = (event.content.questions as Array<{ header?: string }>) ?? [];
			const answers = questions.map((q, idx) => ({
				header: q.header ?? '',
				selected: questionSelections[`${event.event_id}_${idx}`] ?? ''
			}));
			await engineApi.answerAgentQuestion(session.session_id, answers, addDirsArray);
		} finally {
			submittingAnswer = false;
		}
	}

	async function sendUserInput() {
		if (!userInput.trim() || !session) return;
		const message = userInput.trim();
		userInput = '';

		if (isSessionDone) {
			// Session is done — resume via REST endpoint
			sendingPrompt = true;
			try {
				await engineApi.resumeSession(session.session_id, message, addDirsArray);
			} finally {
				sendingPrompt = false;
			}
		} else {
			// Session is running/awaiting_input — pipe via WebSocket
			sendMessage({
				type: 'user_input',
				session_id: session.session_id,
				payload: { message }
			});
		}
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

<div class="flex h-full flex-1 min-w-0 flex-col">
	<!-- Header bar -->
	<div class="flex h-11 items-center justify-between border-b border-surface-700 px-4">
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
	<div bind:this={scrollContainer} class="flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-4 py-3 space-y-3">
		{#if hiddenCount > 0}
			<div class="flex justify-center">
				<button
					class="rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-[11px] text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200"
					onclick={() => (showAll = true)}
				>
					Show {hiddenCount} older messages
				</button>
			</div>
		{/if}

		{#each segments as segment, segIdx}
			{#if segment.kind === 'event'}
				{@const event = segment.event}
				{@const isUser = event.actor === 'user'}
				<div id="event-{event.event_id}" class="flex {isUser ? 'justify-end' : 'justify-start'}">
					<div class="max-w-[80%] rounded-lg px-3 py-2 {isUser ? 'bg-laya-orange/10 text-surface-100' : event.event_type === 'error' ? 'bg-red-900/20 text-red-200' : 'bg-surface-800 text-surface-200'}">

						{#if event.event_type === 'approval_request' && event.content.ask_user_question}
							<!-- AskUserQuestion — interactive multi-question form -->
							{@const questions = (event.content.questions as Array<{header?: string; question?: string; options?: Array<{label: string; description?: string}>; multiSelect?: boolean}>) ?? []}
							{@const isAnswered = answeredQuestionIds.has(event.event_id)}
							{@const formDisabled = isAnswered || isAgentActive}

							<div class="mb-2 flex items-center gap-2">
								<span class="text-xs font-medium text-laya-orange">Agent needs your input</span>
								{#if isAnswered}
									<span class="rounded bg-green-900/40 px-1.5 py-0.5 text-[9px] font-medium text-green-300">Answered</span>
								{:else if isAgentActive}
									<span class="rounded bg-blue-900/40 px-1.5 py-0.5 text-[9px] font-medium text-blue-300">Agent working</span>
								{/if}
							</div>

							<div class="space-y-3">
								{#each questions as q, qIdx}
									{@const selKey = `${event.event_id}_${qIdx}`}
									<div class="rounded border border-surface-600/50 bg-surface-900/50 p-2.5">
										{#if q.header}
											<p class="mb-1 text-[11px] font-semibold text-surface-200">{q.header}</p>
										{/if}
										{#if q.question}
											<p class="mb-2 text-xs text-surface-400">{q.question}</p>
										{/if}
										{#if q.options && q.options.length > 0}
											<div class="space-y-1.5">
												{#each q.options as opt}
													{@const isSelected = questionSelections[selKey] === opt.label}
													<button
														class="flex w-full flex-col items-start rounded-md border px-2.5 py-2 text-left transition-colors {isSelected ? 'border-laya-orange bg-laya-orange/10' : 'border-surface-600 hover:border-surface-500 hover:bg-surface-800'} disabled:opacity-50 disabled:cursor-not-allowed"
														onclick={() => selectOption(event.event_id, qIdx, opt.label)}
														disabled={formDisabled}
													>
														<span class="text-xs font-medium {isSelected ? 'text-laya-orange' : 'text-surface-200'}">
															{opt.label}
														</span>
														{#if opt.description}
															<span class="mt-0.5 text-[11px] text-surface-400">{opt.description}</span>
														{/if}
													</button>
												{/each}
											</div>
										{/if}
									</div>
								{/each}
							</div>

							{#if isAnswered}
								<!-- Already answered — no action needed -->
							{:else if isAgentActive}
								<div class="mt-3 rounded-lg border border-blue-800/40 bg-blue-900/20 px-3 py-2 text-center text-[11px] text-blue-300">
									Waiting for agent to complete the current turn...
								</div>
							{:else}
								{@const allAnswered = questions.every((_, idx) => questionSelections[`${event.event_id}_${idx}`])}
								<div class="mt-3">
									<button
										class="w-full rounded-lg bg-laya-orange/20 py-2 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-40 disabled:cursor-not-allowed"
										onclick={() => submitAnswers(event)}
										disabled={!allAnswered || submittingAnswer}
									>
										{submittingAnswer ? 'Submitting...' : 'Submit answers'}
									</button>
								</div>
							{/if}

						{:else if event.event_type === 'approval_request'}
							<!-- Regular approval request -->
							<div class="mb-2 text-xs text-yellow-300 font-medium">Approval Required</div>
							<div class="prose-plan text-xs">{@html marked(String(event.content.description ?? event.content.message ?? JSON.stringify(event.content)))}</div>
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

						{:else if isPlanEvent(event)}
							<div class="mb-1 flex items-center gap-2">
								<span class="text-xs font-medium text-laya-gold">Implementation Plan</span>
							</div>
							<div class="prose-plan text-xs">
								{@html marked(getPlanText(event))}
							</div>
						{:else}
							<div class="prose-plan text-xs">{@html marked(String(event.content.text ?? event.content.message ?? JSON.stringify(event.content)))}</div>
						{/if}

						<p class="mt-1 text-[10px] text-surface-500">
							{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
						</p>
					</div>
				</div>

			{:else if segment.kind === 'tool_group'}
				{@const groupEvents = segment.events}
				{@const isExpanded = expandedGroups.has(segIdx)}
				<div class="rounded-lg border border-surface-700/60 bg-surface-850 overflow-hidden">
					<button
						class="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-800"
						onclick={() => toggleGroup(segIdx)}
					>
						<svg
							class="h-3 w-3 text-surface-500 transition-transform {isExpanded ? 'rotate-90' : ''}"
							fill="none" stroke="currentColor" viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
						</svg>
						<span class="text-[11px] font-medium text-surface-400">
							{groupEvents.length} tool{groupEvents.length === 1 ? '' : 's'}
						</span>
						{#if !isExpanded}
							<span class="flex-1 truncate text-[10px] text-surface-500">
								{groupEvents.map(e => (e.content.tool as string) ?? e.event_type).join(', ')}
							</span>
						{/if}
						<span class="text-[10px] text-surface-600">
							{new Date(groupEvents[0].timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
						</span>
					</button>
					{#if isExpanded}
						<div class="border-t border-surface-700/40 divide-y divide-surface-700/30">
							{#each groupEvents as ev (ev.event_id)}
								<div id="event-{ev.event_id}" class="flex items-start gap-2 px-3 py-1.5">
									<span class="mt-0.5 flex-shrink-0 text-xs">{toolIcons[ev.event_type] ?? '\u{1F527}'}</span>
									<div class="min-w-0 flex-1">
										<p class="text-xs font-mono text-surface-300 truncate" title={toolLabel(ev)}>
											{toolLabel(ev)}
										</p>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
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

	<!-- Input bar — always visible when session exists, disabled when agent is active -->
	{#if session}
		<div class="border-t border-surface-700 px-4 py-3 space-y-2">
			<!-- Add Path selector — only when agent is not running -->
			{#if !isAgentActive && availableRepos.length > 0}
				{#if showAddPath}
					<div class="rounded-lg border border-surface-600 bg-surface-900 p-2">
						<div class="mb-1.5 flex items-center justify-between">
							<span class="text-[10px] font-semibold uppercase tracking-wider text-surface-400">Add paths to next session</span>
							<button
								class="text-[10px] text-surface-500 hover:text-surface-300"
								onclick={() => { showAddPath = false; }}
							>Close</button>
						</div>
						<div class="space-y-1">
							{#each availableRepos as repo}
								<button
									class="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors
										{selectedAddDirs.has(repo.path)
											? 'bg-laya-orange/15 text-laya-orange'
											: 'text-surface-300 hover:bg-surface-700'}"
									onclick={() => toggleDir(repo.path)}
								>
									<span class="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded border text-[9px]
										{selectedAddDirs.has(repo.path)
											? 'border-laya-orange/50 bg-laya-orange/20 text-laya-orange'
											: 'border-surface-600'}"
									>{selectedAddDirs.has(repo.path) ? '✓' : ''}</span>
									<span class="truncate font-medium">{repo.name}</span>
									<span class="ml-auto truncate text-[10px] text-surface-500 max-w-[200px]">{repo.path}</span>
								</button>
							{/each}
						</div>
					</div>
				{:else}
					<div class="flex items-center gap-2">
						<button
							class="rounded px-2 py-1 text-[11px] text-surface-400 border border-surface-700 hover:border-surface-500 hover:text-surface-200 transition-colors"
							onclick={() => { showAddPath = true; }}
						>
							+ Add Path
						</button>
						{#if selectedAddDirs.size > 0}
							<span class="text-[10px] text-laya-orange">{selectedAddDirs.size} path{selectedAddDirs.size > 1 ? 's' : ''} selected</span>
						{/if}
					</div>
				{/if}
			{/if}

			<div class="flex gap-2">
				<textarea
					bind:value={userInput}
					placeholder={isAgentActive ? 'Agent is working...' : 'Send a message to the agent...'}
					rows="1"
					class="flex-1 resize-none rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-100 placeholder-surface-500 focus:border-blue-600 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
					onkeydown={handleKeydown}
					disabled={isAgentActive || sendingPrompt}
				></textarea>
				<button
					class="rounded-lg bg-blue-700/40 px-4 py-2 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-700/60 disabled:opacity-50 disabled:cursor-not-allowed"
					onclick={sendUserInput}
					disabled={!userInput.trim() || isAgentActive || sendingPrompt}
				>{sendingPrompt ? 'Sending...' : 'Send'}</button>
			</div>
		</div>
	{/if}
</div>
