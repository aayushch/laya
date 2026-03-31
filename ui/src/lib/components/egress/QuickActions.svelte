<script lang="ts">
	import type { EgressCapability } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import InlineEditor from './InlineEditor.svelte';
	import ConfirmAction from './ConfirmAction.svelte';

	let {
		platform,
		cardId,
		eventId,
		metadata = {}
	}: {
		platform: string;
		cardId: string;
		eventId: string;
		metadata: Record<string, unknown>;
	} = $props();

	let capabilities = $state<EgressCapability[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Inline editor state
	let editorOpen = $state(false);
	let editorActionType = $state('');
	let editorPrefill = $state<Record<string, unknown>>({});

	// Confirm action state
	let confirmOpen = $state(false);
	let confirmPreview = $state<import('$lib/api/types').EgressPreviewResponse | null>(null);
	let confirmLoading = $state(false);
	let confirmActionType = $state('');
	let confirmPayload = $state<Record<string, unknown>>({});

	const platformLabels: Record<string, string> = {
		gmail: 'Gmail',
		slack: 'Slack',
		jira: 'Jira',
		github: 'GitHub',
		bitbucket: 'Bitbucket'
	};

	// Action icons (SVG path data)
	const actionIcons: Record<string, string> = {
		reply: 'M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6',
		forward: 'M13 7l5 5m0 0l-5 5m5-5H6',
		comment: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
		archive: 'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4',
		transition: 'M13 9l3 3m0 0l-3 3m3-3H8m13 0a9 9 0 11-18 0 9 9 0 0118 0z',
		approve: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
		merge: 'M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2'
	};

	// Inline-editable actions (open the InlineEditor)
	const inlineActions = new Set(['reply', 'forward', 'comment']);

	$effect(() => {
		loadCapabilities();
	});

	async function loadCapabilities() {
		loading = true;
		error = null;
		try {
			const resp = await engineApi.getEgressCapabilities(platform);
			capabilities = resp.capabilities;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load capabilities';
		} finally {
			loading = false;
		}
	}

	function buildPrefill(actionType: string): Record<string, unknown> {
		const prefill: Record<string, unknown> = { ...metadata };
		if (actionType === 'reply' && platform === 'gmail') {
			prefill.to = metadata.from ?? metadata.actor_email ?? '';
			prefill.subject = metadata.subject
				? `Re: ${String(metadata.subject).replace(/^Re:\s*/i, '')}`
				: '';
		}
		if (actionType === 'forward' && platform === 'gmail') {
			prefill.subject = metadata.subject
				? `Fwd: ${String(metadata.subject).replace(/^Fwd:\s*/i, '')}`
				: '';
			prefill.body = metadata.body ?? '';
		}
		return prefill;
	}

	async function handleAction(cap: EgressCapability) {
		if (inlineActions.has(cap.action_type)) {
			editorActionType = cap.action_type;
			editorPrefill = buildPrefill(cap.action_type);
			editorOpen = true;
		} else {
			// Preview first, then confirm
			confirmActionType = cap.action_type;
			confirmPayload = buildPrefill(cap.action_type);
			confirmLoading = true;
			confirmOpen = true;
			try {
				confirmPreview = await engineApi.egressPreview({
					platform,
					action_type: cap.action_type,
					payload: confirmPayload,
					source_card_id: cardId,
					source_event_id: eventId
				});
			} catch {
				confirmPreview = {
					platform,
					action_type: cap.action_type,
					summary: `Execute ${cap.label}`,
					details: confirmPayload,
					warnings: [],
					estimated_impact: ''
				};
			} finally {
				confirmLoading = false;
			}
		}
	}

	async function executeConfirmed() {
		confirmLoading = true;
		try {
			await engineApi.egressExecute({
				platform,
				action_type: confirmActionType,
				payload: confirmPayload,
				source_card_id: cardId,
				source_event_id: eventId
			});
			confirmOpen = false;
		} catch {
			// Error handling is in ConfirmAction via loading state
		} finally {
			confirmLoading = false;
		}
	}

	function getIcon(actionType: string): string {
		return actionIcons[actionType] ?? actionIcons.comment;
	}
</script>

<div class="space-y-3">
	{#if loading}
		<div class="flex items-center gap-2 text-xs text-surface-500">
			<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
				<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
			</svg>
			Loading actions...
		</div>
	{:else if error}
		<p class="text-xs text-red-400">{error}</p>
	{:else if capabilities.length === 0}
		<p class="text-xs text-surface-500">
			Connect {platformLabels[platform] ?? platform} to enable actions
		</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			{#each capabilities as cap}
				<button
					class="btn btn-sm inline-flex items-center gap-1.5 rounded-md bg-surface-800 px-2.5 py-1.5 text-xs font-medium text-surface-200 transition-colors hover:bg-surface-700"
					onclick={() => handleAction(cap)}
					title={cap.description}
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={getIcon(cap.action_type)} />
					</svg>
					{cap.label}
				</button>
			{/each}
		</div>
	{/if}

	{#if editorOpen}
		<InlineEditor
			{platform}
			actionType={editorActionType}
			prefill={editorPrefill}
			sourceCardId={cardId}
			sourceEventId={eventId}
			onClose={() => { editorOpen = false; }}
		/>
	{/if}

	{#if confirmOpen && confirmPreview}
		<ConfirmAction
			preview={confirmPreview}
			onConfirm={executeConfirmed}
			onCancel={() => { confirmOpen = false; }}
			loading={confirmLoading}
		/>
	{/if}
</div>
