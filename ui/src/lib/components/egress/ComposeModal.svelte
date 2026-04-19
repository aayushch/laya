<script lang="ts">
	import { compose } from '$lib/stores/compose';
	import { engineApi } from '$lib/api/engine';
	import type { EgressConnection } from '$lib/api/types';

	let connections = $state<EgressConnection[]>([]);
	let connectionsLoaded = $state(false);
	let selectedConnectionId = $state<string>('');

	// Form state
	let sending = $state(false);
	let aiAssisting = $state(false);
	let success = $state(false);
	let resultUrl = $state<string | null>(null);
	let error = $state<string | null>(null);

	// Email fields
	let emailTo = $state('');
	let emailCc = $state('');
	let showCc = $state(false);
	let emailSubject = $state('');
	let emailBody = $state('');

	// Slack fields
	let slackChannel = $state('');
	let slackMessage = $state('');
	let slackThreadReply = $state(false);
	let slackThreadTs = $state('');

	// Jira fields
	let jiraProject = $state('');
	let jiraType = $state('Task');
	let jiraSummary = $state('');
	let jiraDescription = $state('');
	let jiraPriority = $state('Medium');

	// GitHub fields
	let ghRepo = $state('');
	let ghTitle = $state('');
	let ghBody = $state('');
	let ghLabels = $state('');

	const platformTabs = [
		{ id: 'gmail', label: 'Gmail', icon: 'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z' },
		{ id: 'slack', label: 'Slack', icon: 'M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5zm0 0H20m-9.5 0c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5S9 17.33 9 16.5v-5c0-.83.67-1.5 1.5-1.5zm0 0H4' },
		{ id: 'jira', label: 'Jira', icon: 'M12 2L2 12l10 10 10-10L12 2z' },
		{ id: 'github', label: 'GitHub', icon: 'M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 00-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0020 4.77 5.07 5.07 0 0019.91 1S18.73.65 16 2.48a13.38 13.38 0 00-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 005 4.77a5.44 5.44 0 00-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 009 18.13V22' }
	];

	const connectedPlatforms = $derived(
		connectionsLoaded
			? platformTabs.filter((t) => connections.some((c) => c.platform === t.id && c.status === 'connected'))
			: platformTabs
	);

	const platformConnections = $derived(
		connections.filter((c) => c.platform === activePlatform && c.status === 'connected')
	);

	let activePlatform = $state('');

	// Sync activePlatform from compose store
	$effect(() => {
		if ($compose.isOpen) {
			activePlatform = $compose.platform || connectedPlatforms[0]?.id || 'gmail';
			prefillFields();
			loadConnections();
		}
	});

	// Auto-select first connection when platform connections change
	$effect(() => {
		if (platformConnections.length > 0 && !platformConnections.find((c) => c.connection_id === selectedConnectionId)) {
			selectedConnectionId = platformConnections[0].connection_id;
		}
	});

	function prefillFields() {
		const pf = $compose.prefill;
		// Email
		emailTo = String(pf.to ?? '');
		emailCc = String(pf.cc ?? '');
		emailSubject = String(pf.subject ?? '');
		emailBody = String(pf.body ?? '');
		showCc = !!pf.cc;
		// Slack
		slackChannel = String(pf.channel ?? '');
		slackMessage = String(pf.message ?? pf.body ?? '');
		slackThreadReply = !!pf.thread_ts;
		slackThreadTs = String(pf.thread_ts ?? '');
		// Jira
		jiraProject = String(pf.project ?? '');
		jiraType = String(pf.type ?? 'Task');
		jiraSummary = String(pf.summary ?? '');
		jiraDescription = String(pf.description ?? pf.body ?? '');
		jiraPriority = String(pf.priority ?? 'Medium');
		// GitHub
		ghRepo = String(pf.repo ?? '');
		ghTitle = String(pf.title ?? '');
		ghBody = String(pf.body ?? '');
		ghLabels = String(pf.labels ?? '');
	}

	async function loadConnections() {
		if (connectionsLoaded) return;
		try {
			const resp = await engineApi.listEgressConnections();
			connections = resp.connections;
		} catch {
			// Silently fail; show all tabs
		} finally {
			connectionsLoaded = true;
		}
	}

	function buildPayload(): Record<string, unknown> {
		switch (activePlatform) {
			case 'gmail':
				return {
					to: emailTo,
					...(emailCc ? { cc: emailCc } : {}),
					subject: emailSubject,
					body: emailBody
				};
			case 'slack': {
				const p: Record<string, unknown> = { channel: slackChannel, body: slackMessage };
				if (slackThreadReply && slackThreadTs) p.thread_ts = slackThreadTs;
				return p;
			}
			case 'jira':
				return {
					project: jiraProject,
					type: jiraType,
					summary: jiraSummary,
					description: jiraDescription,
					priority: jiraPriority
				};
			case 'github':
				return {
					repo: ghRepo,
					title: ghTitle,
					body: ghBody,
					...(ghLabels ? { labels: ghLabels.split(',').map((l) => l.trim()).filter(Boolean) } : {})
				};
			default:
				return {};
		}
	}

	function actionType(): string {
		const storeAction = $compose.actionType;
		switch (activePlatform) {
			case 'gmail':
				if (storeAction === 'reply') return 'reply';
				if (storeAction === 'forward') return 'forward';
				return 'send_email';
			case 'slack':
				return slackThreadReply ? 'reply_thread' : 'send_message';
			case 'jira': return 'create_issue';
			case 'github': return 'create_issue';
			default: return storeAction || 'send_email';
		}
	}

	const submitLabel = $derived(
		activePlatform === 'jira' || activePlatform === 'github' ? 'Create' : 'Send'
	);

	async function aiAssist() {
		aiAssisting = true;
		error = null;
		try {
			const result = await engineApi.egressAiAssist({
				platform: activePlatform,
				action_type: actionType(),
				context: buildPayload()
			});
			const draft = result.draft;
			// Apply drafted fields back to the form
			if (activePlatform === 'gmail') {
				if (draft.body) emailBody = draft.body;
				if (draft.subject && !emailSubject) emailSubject = draft.subject;
			} else if (activePlatform === 'slack') {
				if (draft.message) slackMessage = draft.message;
			} else if (activePlatform === 'jira') {
				if (draft.description) jiraDescription = draft.description;
			} else if (activePlatform === 'github') {
				if (draft.body) ghBody = draft.body;
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'AI assist failed';
		} finally {
			aiAssisting = false;
		}
	}

	async function submit() {
		sending = true;
		error = null;
		try {
			const result = await engineApi.egressExecute({
				platform: activePlatform,
				action_type: actionType(),
				payload: buildPayload(),
				connection_id: selectedConnectionId || undefined,
				source_card_id: $compose.sourceCardId ?? undefined
			});
			success = true;
			resultUrl = result.result_url ?? null;
			setTimeout(() => {
				compose.closeCompose();
				resetState();
			}, 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to send';
		} finally {
			sending = false;
		}
	}

	function resetState() {
		success = false;
		resultUrl = null;
		error = null;
		sending = false;
		aiAssisting = false;
		selectedConnectionId = '';
		emailTo = '';
		emailCc = '';
		emailSubject = '';
		emailBody = '';
		showCc = false;
		slackChannel = '';
		slackMessage = '';
		slackThreadReply = false;
		slackThreadTs = '';
		jiraProject = '';
		jiraType = 'Task';
		jiraSummary = '';
		jiraDescription = '';
		jiraPriority = 'Medium';
		ghRepo = '';
		ghTitle = '';
		ghBody = '';
		ghLabels = '';
	}

	function close() {
		compose.closeCompose();
		resetState();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}

	// Window-level listener: the backdrop div only receives keydown when focus
	// is inside it, but the modal typically opens with focus on the trigger
	// button outside. Listen on window while open so ESC always closes.
	$effect(() => {
		if (!$compose.isOpen) return;
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) close();
	}

	// Common input styles
	const inputClass = 'w-full rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30';
	const labelClass = 'block text-xs font-medium text-surface-400 mb-1';
	const selectClass = 'rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-200 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30';
</script>

{#if $compose.isOpen}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Compose message"
		tabindex="0"
		onclick={handleBackdrop}
		onkeydown={handleKeydown}
	>
		<div class="mx-4 w-full max-w-2xl rounded-xl border border-surface-700 bg-surface-900 shadow-2xl">
			<!-- Header -->
			<div class="flex items-center justify-between border-b border-surface-700 px-5 py-3">
				<h2 class="text-sm font-semibold text-surface-50">Compose</h2>
				<button
					class="rounded p-1 text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					aria-label="Close"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- Platform tabs -->
			<div class="flex gap-0.5 border-b border-surface-700 px-5 pt-2">
				{#each connectedPlatforms as tab}
					<button
						class="inline-flex items-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-medium transition-colors
							{activePlatform === tab.id
								? 'bg-surface-800 text-laya-orange border-b-2 border-laya-orange'
								: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'}"
						onclick={() => { activePlatform = tab.id; error = null; }}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={tab.icon} />
						</svg>
						{tab.label}
					</button>
				{/each}
			</div>

			<!-- Form body -->
			<div class="p-5 space-y-3">
				{#if success}
					<div class="flex items-center gap-2 py-4 text-sm text-green-400">
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
						</svg>
						<span>Sent successfully!</span>
						{#if resultUrl}
							<a
								href={resultUrl}
								target="_blank"
								rel="noopener noreferrer"
								class="ml-1 text-laya-orange hover:text-laya-peach underline underline-offset-2 text-xs"
							>
								View
							</a>
						{/if}
					</div>
				{:else if activePlatform === 'gmail'}
					<!-- Email form -->
					<div>
						<label class={labelClass} for="compose-from">From</label>
						{#if platformConnections.length > 1}
							<select id="compose-from" bind:value={selectedConnectionId} class="{selectClass} w-full">
								{#each platformConnections as conn}
									<option value={conn.connection_id}>{conn.name}</option>
								{/each}
							</select>
						{:else if platformConnections.length === 1}
							<p class="text-sm text-surface-300 px-3 py-2">{platformConnections[0].name}</p>
						{:else}
							<p class="text-sm text-surface-500 italic px-3 py-2">
								{connectionsLoaded ? 'No email accounts connected' : 'Loading accounts...'}
							</p>
						{/if}
					</div>
					<div>
						<label class={labelClass} for="compose-to">To</label>
						<input id="compose-to" type="email" bind:value={emailTo} class={inputClass} placeholder="recipient@example.com" />
					</div>
					{#if showCc}
						<div>
							<label class={labelClass} for="compose-cc">CC</label>
							<input id="compose-cc" type="text" bind:value={emailCc} class={inputClass} placeholder="cc@example.com" />
						</div>
					{:else}
						<button class="text-xs text-surface-500 hover:text-surface-300 transition-colors" onclick={() => (showCc = true)}>
							+ Add CC
						</button>
					{/if}
					<div>
						<label class={labelClass} for="compose-subject">Subject</label>
						<input id="compose-subject" type="text" bind:value={emailSubject} class={inputClass} placeholder="Subject" />
					</div>
					<div>
						<label class={labelClass} for="compose-email-body">Body</label>
						<textarea id="compose-email-body" bind:value={emailBody} rows="8" class="{inputClass} font-mono resize-y" placeholder="Write your email..."></textarea>
					</div>
				{:else if activePlatform === 'slack'}
					<!-- Slack form -->
					<div>
						<label class={labelClass} for="compose-channel">Channel</label>
						<input id="compose-channel" type="text" bind:value={slackChannel} class={inputClass} placeholder="#general" />
					</div>
					<div>
						<label class={labelClass} for="compose-slack-msg">Message</label>
						<textarea id="compose-slack-msg" bind:value={slackMessage} rows="6" class="{inputClass} resize-y" placeholder="Write your message..."></textarea>
					</div>
					<div class="flex items-center gap-2">
						<input id="compose-thread" type="checkbox" bind:checked={slackThreadReply} class="rounded border-surface-600 bg-surface-800 text-laya-orange focus:ring-laya-orange/30" />
						<label for="compose-thread" class="text-xs text-surface-400">Reply to thread</label>
					</div>
					{#if slackThreadReply}
						<div>
							<label class={labelClass} for="compose-thread-ts">Thread timestamp</label>
							<input id="compose-thread-ts" type="text" bind:value={slackThreadTs} class={inputClass} placeholder="1234567890.123456" />
						</div>
					{/if}
				{:else if activePlatform === 'jira'}
					<!-- Jira form -->
					<div class="grid grid-cols-2 gap-3">
						<div>
							<label class={labelClass} for="compose-jira-project">Project</label>
							<input id="compose-jira-project" type="text" bind:value={jiraProject} class={inputClass} placeholder="PROJ" />
						</div>
						<div>
							<label class={labelClass} for="compose-jira-type">Type</label>
							<select id="compose-jira-type" bind:value={jiraType} class="{selectClass} w-full">
								<option value="Bug">Bug</option>
								<option value="Task">Task</option>
								<option value="Story">Story</option>
							</select>
						</div>
					</div>
					<div>
						<label class={labelClass} for="compose-jira-summary">Summary</label>
						<input id="compose-jira-summary" type="text" bind:value={jiraSummary} class={inputClass} placeholder="Issue summary" />
					</div>
					<div>
						<label class={labelClass} for="compose-jira-desc">Description</label>
						<textarea id="compose-jira-desc" bind:value={jiraDescription} rows="6" class="{inputClass} resize-y" placeholder="Describe the issue..."></textarea>
					</div>
					<div>
						<label class={labelClass} for="compose-jira-priority">Priority</label>
						<select id="compose-jira-priority" bind:value={jiraPriority} class="{selectClass} w-full">
							<option value="Highest">Highest</option>
							<option value="High">High</option>
							<option value="Medium">Medium</option>
							<option value="Low">Low</option>
							<option value="Lowest">Lowest</option>
						</select>
					</div>
				{:else if activePlatform === 'github'}
					<!-- GitHub form -->
					<div>
						<label class={labelClass} for="compose-gh-repo">Repository</label>
						<input id="compose-gh-repo" type="text" bind:value={ghRepo} class={inputClass} placeholder="owner/repo" />
					</div>
					<div>
						<label class={labelClass} for="compose-gh-title">Title</label>
						<input id="compose-gh-title" type="text" bind:value={ghTitle} class={inputClass} placeholder="Issue title" />
					</div>
					<div>
						<label class={labelClass} for="compose-gh-body">Body</label>
						<textarea id="compose-gh-body" bind:value={ghBody} rows="6" class="{inputClass} resize-y" placeholder="Describe the issue..."></textarea>
					</div>
					<div>
						<label class={labelClass} for="compose-gh-labels">Labels</label>
						<input id="compose-gh-labels" type="text" bind:value={ghLabels} class={inputClass} placeholder="bug, enhancement (comma-separated)" />
					</div>
				{/if}

				{#if error}
					<p class="text-xs text-red-400">{error}</p>
				{/if}
			</div>

			<!-- Footer -->
			{#if !success}
				<div class="flex items-center justify-between border-t border-surface-700 px-5 py-3">
					<button
						class="inline-flex items-center gap-1.5 rounded-md bg-surface-800 px-3 py-1.5 text-xs font-medium transition-colors
							{aiAssisting ? 'text-laya-orange cursor-wait' : 'text-surface-400 hover:text-laya-orange hover:bg-surface-700'}"
						onclick={aiAssist}
						disabled={aiAssisting || sending}
						title="Generate a draft with AI"
					>
						{#if aiAssisting}
							<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
							</svg>
							Drafting...
						{:else}
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
							</svg>
							AI Assist
						{/if}
					</button>
					<div class="flex items-center gap-2">
						<button
							class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
							onclick={close}
							disabled={sending}
						>
							Cancel
						</button>
						<button
							class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-4 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50 disabled:cursor-not-allowed"
							onclick={submit}
							disabled={sending}
						>
							{#if sending}
								<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
									<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
									<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
								</svg>
								Sending...
							{:else}
								{submitLabel}
							{/if}
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}
