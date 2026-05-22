<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { ActionCard, WorkspaceEvent, WorkspaceSession } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { sendMessage } from '$lib/stores/websocket';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { tick } from 'svelte';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	let {
		card,
		session,
		events,
		timelineOpen = false,
		selectedAddDirs = new Set<string>(),
		ontoggletime
	}: {
		card: ActionCard;
		session: WorkspaceSession | null;
		events: WorkspaceEvent[];
		timelineOpen?: boolean;
		selectedAddDirs?: Set<string>;
		ontoggletime?: () => void;
	} = $props();

	const VISIBLE_LIMIT = 150;
	let showAll = $state(false);

	let userInput = $state('');
	let approvalReason = $state('');
	let showDenyInput = $state<string | null>(null);
	let scrollContainer = $state<HTMLElement | null>(null);
	let sendingPrompt = $state(false);

	// Agent permission mode toggle — 'plan' or 'acceptEdits'
	let agentMode = $state<'plan' | 'acceptEdits'>(
		(session?.permission_mode as 'plan' | 'acceptEdits') || 'plan'
	);

	// Sync agentMode when session changes (e.g. after poll refresh updates permission_mode)
	$effect(() => {
		const mode = session?.permission_mode;
		if (mode) {
			agentMode = mode as 'plan' | 'acceptEdits';
		}
	});

	// Toggle is enabled when the agent is NOT actively running
	const canToggleMode = $derived(
		session != null && !['starting', 'running'].includes(session.status)
	);

	// AskUserQuestion state
	let questionSelections = $state<Record<string, string>>({});
	let submittingAnswer = $state(false);
	let dismissingQuestions = $state(false);

	const addDirsArray = $derived(
		selectedAddDirs.size > 0 ? [...selectedAddDirs] : undefined
	);

	const AGENT_EVENT_TYPES = new Set([
		'agent_message',
		'approval_request',
		'user_response',
		'questions_dismissed',
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

	// Track questions dismissed via the dismiss-questions action
	const dismissedQuestionIds = $derived.by(() => {
		const ids = new Set<string>();
		for (let i = 0; i < events.length; i++) {
			if (events[i].event_type === 'questions_dismissed') {
				// All prior unanswered questions are now dismissed
				for (let j = 0; j < i; j++) {
					const ev = events[j];
					if (
						ev.event_type === 'approval_request' &&
						ev.content.ask_user_question &&
						!answeredQuestionIds.has(ev.event_id)
					) {
						ids.add(ev.event_id);
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

	const sessionStatusColors = $derived<Record<string, string>>($glassTheme ? {
		starting: 'backdrop-blur-sm bg-cyan-400/15 text-cyan-300',
		running: 'backdrop-blur-sm bg-blue-400/15 text-blue-300',
		awaiting_input: 'backdrop-blur-sm bg-violet-400/15 text-violet-300',
		paused: 'backdrop-blur-sm bg-white/[0.08] text-surface-300',
		completed: 'backdrop-blur-sm bg-green-400/15 text-green-300',
		failed: 'backdrop-blur-sm bg-red-400/15 text-red-300',
		cancelled: 'backdrop-blur-sm bg-white/[0.08] text-surface-400'
	} : {
		starting: 'bg-cyan-900/50 text-cyan-300',
		running: 'bg-blue-900/50 text-blue-300',
		awaiting_input: 'bg-violet-900/50 text-violet-300',
		paused: 'bg-surface-700 text-surface-300',
		completed: 'bg-green-900/50 text-green-300',
		failed: 'bg-red-900/50 text-red-300',
		cancelled: 'bg-surface-700 text-surface-400'
	});

	const toolIconPaths: Record<string, string> = {
		file_read: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm-2 1.5L18.5 10H12V3.5ZM8 13h8v1.5H8V13Zm0 3h5v1.5H8V16Z',
		file_write: 'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25ZM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z',
		tool_call: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76Z'
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
			await engineApi.answerAgentQuestion(session.session_id, answers, addDirsArray, agentMode);
		} finally {
			submittingAnswer = false;
		}
	}

	async function dismissQuestions() {
		if (!session) return;
		dismissingQuestions = true;
		try {
			await engineApi.dismissQuestions(session.session_id);
		} finally {
			dismissingQuestions = false;
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
				await engineApi.resumeSession(session.session_id, message, addDirsArray, agentMode);
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

<div class="flex h-full flex-1 min-w-0 flex-col border-l border-t {$glassTheme ? 'glass-panel border-white/[0.06]' : 'border-surface-700 bg-surface-900'}">
	<!-- Header bar — solid bg in non-glass mode prevents title clipping during macOS elastic overscroll -->
	<div class="relative z-10 flex h-11 items-center justify-between border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700 bg-surface-900'} px-4 gap-3">
		<div class="flex min-w-0 flex-1 items-center gap-1.5">
			{#if ontoggletime}
				<button
					class="shrink-0 text-laya-orange transition-transform duration-200"
					onclick={ontoggletime}
					title="{timelineOpen ? 'Hide' : 'Show'} timeline"
				>
					<svg class="h-4 w-4 transition-transform duration-200 {timelineOpen ? '' : 'rotate-180'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7" />
					</svg>
				</button>
			{/if}
			<h2 class="min-w-0 flex-1 truncate text-sm font-semibold text-surface-100">{card.header}</h2>
		</div>

		<div class="flex shrink-0 items-center gap-2">
			{#if session}
				<!-- Plan / Act mode toggle -->
				<div class="flex rounded text-[10px] font-medium overflow-hidden {canToggleMode ? '' : 'opacity-50 pointer-events-none'}">
					<button
						class="px-2 py-0.5 transition-colors {agentMode === 'plan' ? ($glassTheme ? 'backdrop-blur-sm bg-blue-400/15 text-blue-300' : 'bg-blue-900/50 text-blue-300') : $glassTheme ? 'bg-white/[0.04] text-surface-400 hover:text-surface-200' : 'bg-surface-800 text-surface-400 hover:text-surface-200'}"
						onclick={() => (agentMode = 'plan')}
						disabled={!canToggleMode}
					>Plan</button>
					<button
						class="px-2 py-0.5 transition-colors {agentMode === 'acceptEdits' ? ($glassTheme ? 'backdrop-blur-sm bg-amber-400/15 text-amber-300' : 'bg-amber-900/50 text-amber-300') : $glassTheme ? 'bg-white/[0.04] text-surface-400 hover:text-surface-200' : 'bg-surface-800 text-surface-400 hover:text-surface-200'}"
						onclick={() => (agentMode = 'acceptEdits')}
						disabled={!canToggleMode}
					>Act</button>
				</div>

				<span class="rounded px-1.5 py-0.5 text-[10px] font-medium {sessionStatusColors[session.status] ?? 'bg-surface-700 text-surface-300'}">
					{session.status}
				</span>
				<span class="text-[10px] text-surface-500">{session.agent_type}</span>
			{/if}

			{#if session && !['completed', 'failed', 'cancelled'].includes(session.status)}
				<div class="ml-1 flex gap-1">
					{#if session.status === 'paused'}
						<button
							class="rounded px-2 py-1 text-[11px] text-surface-300 {$glassTheme ? 'hover:bg-white/[0.06]' : 'hover:bg-surface-700'}"
							onclick={() => controlSession('resume')}
						>Resume</button>
					{:else}
						<button
							class="rounded px-2 py-1 text-[11px] text-surface-300 {$glassTheme ? 'hover:bg-white/[0.06]' : 'hover:bg-surface-700'}"
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
	</div>

	<!-- Event stream -->
	<div bind:this={scrollContainer} class="flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-4 py-3 space-y-3">
		{#if hiddenCount > 0}
			<div class="flex justify-center">
				<button
					class="rounded-lg border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04] hover:bg-white/[0.06]' : 'border-surface-600 bg-surface-800 hover:bg-surface-700'} px-3 py-1.5 text-[11px] text-surface-400 transition-colors hover:text-surface-200"
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
					<div class="max-w-[80%] rounded-lg px-3 py-2 {isUser ? 'bg-laya-orange/10 text-surface-100' : event.event_type === 'error' ? 'bg-red-900/20 text-red-200' : ($glassTheme ? 'bg-white/[0.04] text-surface-200' : 'bg-surface-800 text-surface-200')}">

						{#if event.event_type === 'approval_request' && event.content.ask_user_question}
							<!-- AskUserQuestion — interactive multi-question form -->
							{@const questions = (event.content.questions as Array<{header?: string; question?: string; options?: Array<{label: string; description?: string}>; multiSelect?: boolean}>) ?? []}
							{@const isAnswered = answeredQuestionIds.has(event.event_id)}
							{@const isDismissed = dismissedQuestionIds.has(event.event_id)}
							{@const formDisabled = isAnswered || isDismissed || isAgentActive}

							<div class="mb-2 flex items-center gap-2">
								<span class="text-xs font-medium text-laya-orange">Agent needs your input</span>
								{#if isAnswered}
									<span class="rounded bg-green-900/40 px-1.5 py-0.5 text-[9px] font-medium text-green-300">Answered</span>
								{:else if isDismissed}
									<span class="rounded bg-surface-700 px-1.5 py-0.5 text-[9px] font-medium text-surface-400">Dismissed</span>
								{:else if isAgentActive}
									<span class="rounded bg-blue-900/40 px-1.5 py-0.5 text-[9px] font-medium text-blue-300">Agent working</span>
								{/if}
							</div>

							<div class="space-y-3">
								{#each questions as q, qIdx}
									{@const selKey = `${event.event_id}_${qIdx}`}
									<div class="rounded border p-2.5 {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600/50 bg-surface-900/50'}">
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
														class="flex w-full flex-col items-start rounded-md border px-2.5 py-2 text-left transition-colors {isSelected ? 'border-laya-orange bg-laya-orange/10' : $glassTheme ? 'border-white/[0.08] hover:border-white/[0.14] hover:bg-white/[0.06]' : 'border-surface-600 hover:border-surface-500 hover:bg-surface-800'} disabled:opacity-50 disabled:cursor-not-allowed"
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

							{#if isAnswered || isDismissed}
								<!-- Already answered or dismissed — no action needed -->
							{:else if isAgentActive}
								<div class="mt-3 rounded-lg border border-blue-800/40 bg-blue-900/20 px-3 py-2 text-center text-[11px] text-blue-300">
									Waiting for agent to complete the current turn...
								</div>
							{:else}
								{@const allAnswered = questions.every((_, idx) => questionSelections[`${event.event_id}_${idx}`])}
								<div class="mt-3 space-y-1.5">
									<button
										class="w-full rounded-lg bg-laya-orange/20 py-2 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-40 disabled:cursor-not-allowed"
										onclick={() => submitAnswers(event)}
										disabled={!allAnswered || submittingAnswer}
									>
										{submittingAnswer ? 'Submitting...' : 'Submit answers'}
									</button>
									<button
										class="w-full rounded-lg border py-2 text-xs font-medium text-surface-400 transition-colors hover:text-surface-200 disabled:opacity-40 disabled:cursor-not-allowed {$glassTheme ? 'border-white/[0.08] hover:bg-white/[0.06]' : 'border-surface-600 hover:bg-surface-800'}"
										onclick={dismissQuestions}
										disabled={dismissingQuestions}
									>
										{dismissingQuestions ? 'Dismissing...' : 'Skip questions'}
									</button>
								</div>
							{/if}

						{:else if event.event_type === 'approval_request'}
							<!-- Regular approval request -->
							<div class="mb-2 text-xs text-yellow-300 font-medium">Approval Required</div>
							<div class="prose-plan break-words text-xs">{@html DOMPurify.sanitize(marked(String(event.content.description ?? event.content.message ?? JSON.stringify(event.content))) as string)}</div>
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

						{:else if event.event_type === 'questions_dismissed'}
						<div class="text-xs italic text-surface-400">Questions dismissed</div>

					{:else if isPlanEvent(event)}
							<div class="mb-1 flex items-center gap-2">
								<span class="text-xs font-medium text-laya-gold">Implementation Plan</span>
							</div>
							<div class="prose-plan break-words text-xs">
								{@html DOMPurify.sanitize(marked(getPlanText(event)) as string)}
							</div>
						{:else}
							<div class="prose-plan break-words text-xs">{@html DOMPurify.sanitize(marked(String(event.content.text ?? event.content.message ?? JSON.stringify(event.content))) as string)}</div>
						{/if}

						<p class="mt-1 text-[10px] text-surface-500">
							{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
						</p>
					</div>
				</div>

			{:else if segment.kind === 'tool_group'}
				{@const groupEvents = segment.events}
				{@const isExpanded = expandedGroups.has(segIdx)}
				{@const toolNames = groupEvents.map(e => (e.content.tool as string) ?? e.event_type)}
				{@const maxPreview = 3}
				{@const previewNames = toolNames.slice(0, maxPreview)}
				{@const remaining = toolNames.length - maxPreview}
				<!-- Compact inline tool hint — minimal footprint between chat bubbles -->
				<div class="px-2 py-0.5">
					<button
						class="inline-flex min-w-0 max-w-full items-center gap-1.5 text-left transition-colors hover:text-surface-300"
						onclick={() => toggleGroup(segIdx)}
					>
						<svg
							class="h-2.5 w-2.5 shrink-0 text-surface-600 transition-transform {isExpanded ? 'rotate-90' : ''}"
							fill="none" stroke="currentColor" viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
						</svg>
						<span class="text-[10px] text-surface-600">
							{groupEvents.length} tool{groupEvents.length === 1 ? '' : 's'}
						</span>
						{#if !isExpanded}
							<span class="truncate text-[10px] text-surface-600">
								{previewNames.join(', ')}{remaining > 0 ? `, +${remaining} more` : ''}
							</span>
						{/if}
					</button>
					{#if isExpanded}
						<div class="mt-1 ml-4 space-y-0.5">
							{#each groupEvents as ev (ev.event_id)}
								<div id="event-{ev.event_id}" class="flex items-center gap-1.5">
									<svg class="h-3 w-3 shrink-0 text-surface-500" viewBox="0 0 24 24" fill="currentColor">
										<path d={toolIconPaths[ev.event_type] ?? toolIconPaths.tool_call} />
									</svg>
									<p class="truncate text-[10px] font-mono text-surface-500" title={toolLabel(ev)}>
										{toolLabel(ev)}
									</p>
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

	<!-- Input bar — seamless bottom area, no extra borders around the field -->
	{#if session}
		<div class="flex gap-2 px-4 py-3 {$glassTheme ? '' : 'bg-surface-900'}">
			<textarea
				bind:value={userInput}
				placeholder={isAgentActive ? 'Agent is working...' : 'Send a message to the agent...'}
				rows="1"
				class="flex-1 resize-none rounded-lg px-3 py-2 text-sm text-surface-100 placeholder-surface-500 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed {$glassTheme ? 'glass-input' : 'border border-transparent bg-surface-800 focus:ring-1 focus:ring-laya-orange/40'}"
				onkeydown={handleKeydown}
				disabled={isAgentActive || sendingPrompt}
			></textarea>
			<button
				class="rounded-lg bg-laya-orange px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-laya-coral disabled:opacity-50 disabled:cursor-not-allowed"
				onclick={sendUserInput}
				disabled={!userInput.trim() || isAgentActive || sendingPrompt}
			>{sendingPrompt ? 'Sending...' : 'Send'}</button>
		</div>
	{/if}
</div>
