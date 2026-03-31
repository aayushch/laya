<script lang="ts">
	import { engineApi } from '$lib/api/engine';

	let {
		platform,
		actionType,
		prefill = {},
		sourceCardId,
		sourceEventId,
		onClose
	}: {
		platform: string;
		actionType: string;
		prefill: Record<string, unknown>;
		sourceCardId?: string;
		sourceEventId?: string;
		onClose: () => void;
	} = $props();

	let body = $state(String(prefill.body ?? prefill.comment ?? ''));
	let to = $state(String(prefill.to ?? ''));
	let subject = $state(String(prefill.subject ?? ''));

	let sending = $state(false);
	let success = $state(false);
	let resultUrl = $state<string | null>(null);
	let error = $state<string | null>(null);

	const isEmail = $derived(platform === 'gmail');

	const buttonLabel = $derived(
		actionType === 'reply' || actionType === 'forward'
			? 'Send'
			: actionType === 'comment'
				? 'Post'
				: 'Send'
	);

	async function submit() {
		sending = true;
		error = null;
		try {
			const payload: Record<string, unknown> = { body };
			if (isEmail) {
				payload.to = to;
				payload.subject = subject;
			}

			const result = await engineApi.egressExecute({
				platform,
				action_type: actionType,
				payload,
				source_card_id: sourceCardId,
				source_event_id: sourceEventId
			});

			success = true;
			resultUrl = result.result_url ?? null;

			// Auto-close after 2 seconds
			setTimeout(() => {
				onClose();
			}, 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to send';
		} finally {
			sending = false;
		}
	}
</script>

<div class="rounded-lg border border-surface-700 bg-surface-800/50 p-4 space-y-3">
	{#if success}
		<!-- Success state -->
		<div class="flex items-center gap-2 text-sm text-green-400">
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
			</svg>
			<span>Sent successfully</span>
			{#if resultUrl}
				<a
					href={resultUrl}
					target="_blank"
					rel="noopener noreferrer"
					class="ml-1 text-laya-orange hover:text-laya-peach underline underline-offset-2 text-xs"
				>
					View
					<svg class="inline h-3 w-3 ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
					</svg>
				</a>
			{/if}
		</div>
	{:else}
		<!-- Email-specific header fields -->
		{#if isEmail}
			<div class="space-y-2">
				<div class="flex items-center gap-2">
					<label class="w-14 text-right text-xs font-medium text-surface-400" for="egress-to">To</label>
					<input
						id="egress-to"
						type="text"
						value={to}
						readonly
						class="flex-1 rounded-md border border-surface-600 bg-surface-900 px-2.5 py-1.5 text-xs text-surface-300 placeholder-surface-600"
					/>
				</div>
				<div class="flex items-center gap-2">
					<label class="w-14 text-right text-xs font-medium text-surface-400" for="egress-subject">Subject</label>
					<input
						id="egress-subject"
						type="text"
						bind:value={subject}
						class="flex-1 rounded-md border border-surface-600 bg-surface-900 px-2.5 py-1.5 text-xs text-surface-200 placeholder-surface-600"
						placeholder="Subject"
					/>
				</div>
			</div>
		{/if}

		<!-- Body textarea -->
		<textarea
			bind:value={body}
			rows="6"
			class="w-full resize-y rounded-md border border-surface-600 bg-surface-900 px-3 py-2 font-mono text-xs text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30"
			placeholder={actionType === 'comment' ? 'Write your comment...' : 'Write your message...'}
		></textarea>

		{#if error}
			<p class="text-xs text-red-400">{error}</p>
		{/if}

		<!-- Action buttons -->
		<div class="flex items-center justify-end gap-2">
			<button
				class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
				onclick={onClose}
				disabled={sending}
			>
				Cancel
			</button>
			<button
				class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-3 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50 disabled:cursor-not-allowed"
				onclick={submit}
				disabled={sending || !body.trim()}
			>
				{#if sending}
					<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
					</svg>
					Sending...
				{:else}
					{buttonLabel}
				{/if}
			</button>
		</div>
	{/if}
</div>
