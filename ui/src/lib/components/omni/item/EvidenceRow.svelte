<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import type { OmniEvidenceCard, SuggestedAction } from '$lib/api/types';
	import MarkdownRender from '$lib/components/MarkdownRender.svelte';
	import { BUCKET_STATUS_LABELS, BUCKET_TOKEN, cardBucket } from '$lib/omni/buckets';
	import {
		OUTPUT_TYPE_LABELS,
		TERMINAL_CARD_STATUSES,
		cardPlatform,
		getEditableTextField,
		type EvidenceActionContext
	} from '$lib/omni/evidenceActions';
	import { hhmm, priorityToken } from '$lib/omni/layers';
	import {
		actorAvatarColor,
		actorInitials,
		platformDotColor,
		platformLabel,
		PRIORITY_LABELS
	} from '$lib/utils/cardVisuals';
	import { cardDescriptions } from '$lib/stores/cardDescriptions';
	import OmniTooltip, { anchorIfTruncated, type TooltipState } from '../OmniTooltip.svelte';

	let {
		card,
		expanded,
		onToggle,
		actions
	}: {
		card: OmniEvidenceCard;
		expanded: boolean;
		onToggle: () => void;
		actions: EvidenceActionContext;
	} = $props();

	let tooltip = $state<TooltipState | null>(null);
	let titleEl = $state<HTMLSpanElement | null>(null);

	const bucket = $derived(cardBucket(card));
	const token = $derived(BUCKET_TOKEN[bucket]);
	const platform = $derived(cardPlatform(card));
	const isTerminal = $derived(TERMINAL_CARD_STATUSES.has(card.status));

	// The draft that staged_output already renders — kept so the Suggested Actions
	// block below doesn't render the same draft a second time.
	const draftAction = $derived(
		card.suggested_actions?.find(
			(a) => a.payload && getEditableTextField(a.payload as Record<string, unknown>)
		) ?? (card.staged_output?.type === 'draft_reply' ? (card.suggested_actions?.[0] ?? null) : null)
	);
</script>

<button
	type="button"
	class="om-row flex w-full items-center gap-2.5 px-2.5 text-left {expanded ? 'om-row--open' : ''}"
	style="padding-block: calc(8px * var(--om-density));"
	data-omni-item={card.card_id}
	aria-expanded={expanded}
	onclick={onToggle}
>
	<span
		class="h-1.5 w-1.5 flex-none rounded-full"
		style="background: {platformDotColor(platform)};"
	></span>

	<span
		class="om-ref w-[112px] flex-none truncate"
		style="color: var(--om-text-body);"
		title={card.source_ref ?? ''}
	>{card.source_ref ?? card.card_id.slice(0, 10)}</span>

	<span
		bind:this={titleEl}
		class="om-row-t min-w-0 flex-1 truncate"
		style="color: {expanded ? 'var(--om-text)' : 'var(--om-text-body)'};"
		onmouseenter={() => (tooltip = anchorIfTruncated(titleEl, card.header))}
		onmouseleave={() => (tooltip = null)}
		role="presentation"
	>{card.header}</span>

	{#if card.actor_name}
		<span
			class="om-hint inline-flex flex-none items-center gap-1.5"
			style="color: var(--om-text-meta);"
		>
			<span
				class="flex h-[15px] w-[15px] items-center justify-center rounded-full text-[calc(8px*var(--om-scale))] font-semibold text-white"
				style="background: {actorAvatarColor(card.actor_name)};"
			>{actorInitials(card.actor_name)}</span>
			<span class="hidden sm:inline">{card.actor_name.split(' ')[0]}</span>
		</span>
	{/if}

	<span
		class="om-status flex-none rounded-[3px] px-1.5 py-0.5"
		style="background: var(--om-{token}-bg); color: var(--om-{token}-fg);"
	>{BUCKET_STATUS_LABELS[bucket]}</span>

	<span class="om-stamp w-9 flex-none text-right" style="color: var(--om-text-faint);">
		{hhmm(card.created_at)}
	</span>

	<span
		class="om-chev flex-none text-[calc(13px*var(--om-scale))] {expanded ? 'om-chev--open' : ''}"
		style="color: var(--om-text-meta);"
		aria-hidden="true">›</span
	>
</button>

{#if expanded}
	<!-- The existing insight panel, unchanged in content: meta bar, header,
	     summary, intelligence report, staged output with Edit → Polish, and the
	     Suggested Actions execute flow. -->
	<div class="om-detail om-glass mx-2.5 mt-0.5 mb-[7px] rounded-[9px] px-[15px] pt-[11px] pb-3">
		<div class="mb-[9px] flex flex-wrap items-center gap-2">
			<span
				class="om-status rounded-[3px] px-1.5 py-0.5"
				style="background: var(--om-pri-{priorityToken(card.priority)}-bg);
					color: var(--om-pri-{priorityToken(card.priority)}-fg);"
			>{PRIORITY_LABELS[card.priority] ?? card.priority}</span>
			<span
				class="om-mono text-[calc(9px*var(--om-scale))] tracking-[0.1em] uppercase"
				style="color: var(--om-text-meta);"
			>{card.persona} · {card.category} · {platformLabel(platform)}</span>
			<span
				class="rounded-[3px] px-1.5 py-0.5 text-[calc(9px*var(--om-scale))]"
				style="background: var(--om-chip); color: var(--om-text-mid);"
			>{card.status}</span>
			<span class="flex-1"></span>
			<button
				type="button"
				class="om-hint transition-colors"
				style="color: var(--om-comp-label);"
				onclick={() => actions.showInPulse(card.card_id)}
			>Show in Pulse</button>
			{#if card.source_url}
				<a
					href={card.source_url}
					target="_blank"
					rel="noopener noreferrer"
					class="om-hint transition-colors"
					style="color: var(--om-comp-label);"
				>Open on {platformLabel(platform)} ↗</a>
			{/if}
		</div>

		<h3
			class="mb-1.5 text-[calc(14.5px*var(--om-scale))] leading-[1.35] font-semibold tracking-[-0.01em] [overflow-wrap:anywhere]"
			style="color: var(--om-text);"
		>{card.header}</h3>

		<!-- Summary follows the "Show Card Descriptions" setting, the same body text
		     it hides on feed cards. The intelligence bullets below are analysis, not
		     description, so they stay. -->
		{#if card.summary && $cardDescriptions}
			<p class="om-body mb-2.5 max-w-[900px] [overflow-wrap:anywhere]" style="color: var(--om-text-body);">
				{card.summary}
			</p>
		{/if}

		{#if card.intelligence && card.intelligence.length > 0}
			<div class="om-micro mb-1.5">Intelligence report</div>
			<div class="mb-3 flex flex-col gap-[5px]">
				{#each card.intelligence as point}
					<div
						class="om-item-t flex items-start gap-2 leading-[1.5]"
						style="color: var(--om-text-body);"
					>
						<span
							class="mt-[6px] h-1 w-1 flex-none rounded-full"
							style="background: var(--om-text-faint);"
						></span>{point}
					</div>
				{/each}
			</div>
		{/if}

		<!-- Editable draft preview — identical controls to CardDetail. Rendered in
		     place of the read-only staged_output markdown for draft_reply so Edit
		     and Polish land where the user expects them. -->
		{#snippet draftPreview(action: SuggestedAction)}
			{@const payload = action.payload as Record<string, unknown>}
			{@const detectedField = getEditableTextField(payload)}
			{@const isDraftReply = card.staged_output?.type === 'draft_reply'}
			{@const fallbackText = isDraftReply ? (card.staged_output?.content ?? '') : ''}
			{@const editableField = detectedField ?? (fallbackText ? 'body' : null)}
			{@const displayText = (detectedField ? (payload[detectedField] as string) : fallbackText) ?? ''}
			{#if editableField && displayText}
				{@const isEditing = actions.editingActionId === action.action_id}
				{@const isPolishing = actions.polishingActionIds.has(action.action_id)}
				{@const hasEdits = payload._edited === true}
				{@const polishErrorMsg = actions.polishErrors[action.action_id]}
				<div class="om-inset relative rounded-lg px-3 py-[11px]">
					{#if !isEditing}
						{#each Object.entries(payload) as [key, value]}
							{#if !key.startsWith('_') && typeof value === 'string' && value.length > 0 && key !== editableField && key !== 'raw'}
								<div class="om-entry-t mb-1.5 flex items-center gap-1.5">
									<span class="font-medium capitalize" style="color: var(--om-text-meta);">{key}:</span>
									<span style="color: var(--om-text-body);">{value}</span>
								</div>
							{/if}
						{/each}
						<div
							class="om-entry-t max-h-96 overflow-y-auto whitespace-pre-wrap"
							style="color: var(--om-text-body);"
						>{displayText}</div>
					{:else}
						{#each Object.entries(actions.editedPayload) as [key]}
							{#if key !== editableField}
								<div class="om-entry-t mb-1.5 flex items-center gap-1.5">
									<span class="shrink-0 font-medium capitalize" style="color: var(--om-text-meta);">{key}:</span>
									<input
										type="text"
										class="om-entry-t w-full rounded px-1.5 py-0.5 outline-none"
										style="border: 1px solid var(--om-border-input); background: var(--om-inset); color: var(--om-text-body);"
										bind:value={actions.editedPayload[key]}
									/>
								</div>
							{/if}
						{/each}
						<textarea
							class="om-entry-t w-full resize-y rounded p-2 outline-none"
							style="border: 1px solid var(--om-border-input); background: var(--om-inset); color: var(--om-text-body);"
							rows="8"
							bind:value={actions.editedPayload[editableField]}
						></textarea>
					{/if}

					{#if isPolishing}
						<div
							class="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg backdrop-blur-sm"
							style="background: color-mix(in oklch, var(--om-detail) 70%, transparent);"
						>
							<svg class="text-laya-orange h-6 w-6 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
							</svg>
							<span class="text-laya-orange om-entry-t font-medium">Polishing draft…</span>
						</div>
					{/if}

					{#if !isTerminal}
						<div class="mt-2 flex items-center justify-end gap-3.5">
							{#if polishErrorMsg && !isPolishing}
								<span class="om-entry-t mr-auto" style="color: var(--om-alert-fg);">{polishErrorMsg}</span>
							{/if}
							{#if !isEditing}
								<button
									type="button"
									class="om-pill-t transition-colors disabled:opacity-40"
									style="color: var(--om-text-dim);"
									onclick={() => actions.startEditing(action, detectedField ? undefined : fallbackText)}
									disabled={isPolishing}
								>Edit draft</button>
								{#if hasEdits}
									<button
										type="button"
										class="om-pill-t inline-flex items-center gap-1 font-semibold transition-colors disabled:opacity-40"
										style="color: var(--color-laya-gold);"
										onclick={() => actions.polishDraft(card, action)}
										disabled={isPolishing}
										title="Rewrite this draft with AI to polish the phrasing"
									>
										<svg class="h-3 w-3" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
											<path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9L12 2zm7 11l.95 2.8L22.75 16.75l-2.8.95L19 20.5l-.95-2.8L15.25 16.75l2.8-.95L19 13zM5 14l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7L5 14z" />
										</svg>
										Polish
									</button>
								{/if}
							{:else}
								<button
									type="button"
									class="om-pill-t transition-colors"
									style="color: var(--om-text-dim);"
									onclick={actions.cancelEditing}
									disabled={actions.savingPayload}
								>Cancel</button>
								<button
									type="button"
									class="om-pill-t font-medium transition-colors disabled:opacity-50"
									style="color: var(--om-comp-num);"
									onclick={() => actions.savePayload(card, action)}
									disabled={actions.savingPayload}
								>{actions.savingPayload ? 'Saving…' : 'Save'}</button>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		{/snippet}

		{#if card.staged_output}
			<div class="om-micro mb-1.5">
				{OUTPUT_TYPE_LABELS[card.staged_output.type] ?? 'Output'}
			</div>
			<div class="mb-3">
				{#if card.staged_output.type === 'code_fix'}
					<pre
						class="om-inset om-entry-t overflow-x-auto rounded-lg px-3 py-[11px]"
						style="color: var(--om-text-body);">{card.staged_output.content}</pre>
				{:else if card.staged_output.type === 'draft_reply' && draftAction}
					{@render draftPreview(draftAction)}
				{:else}
					<MarkdownRender
						content={card.staged_output.content}
						class="om-inset om-body overflow-x-auto rounded-lg p-3"
					/>
				{/if}
			</div>
		{/if}

		{#if card.suggested_actions && card.suggested_actions.length > 0}
			<div class="om-micro mb-1.5">Suggested actions</div>
			{#each card.suggested_actions as action (action.action_id)}
				{#if card.staged_output?.type !== 'draft_reply' || action.action_id !== draftAction?.action_id}
					<div class="mb-2">{@render draftPreview(action)}</div>
				{/if}
			{/each}
			<div class="flex flex-wrap gap-2">
				{#each card.suggested_actions as action (action.action_id)}
					{@const isSelected = card.selected_action_id === action.action_id}
					{@const dimmed = !isSelected && !!card.selected_action_id}
					<button
						type="button"
						class="om-row-t rounded-lg px-3 py-1.5 font-medium transition-colors disabled:cursor-not-allowed {dimmed
							? 'opacity-50'
							: ''}"
						style={isSelected
							? 'border: 1px solid var(--om-accent-border); background: var(--om-comp-bg); color: var(--om-comp-num);'
							: 'border: 1px solid var(--om-border-pill); color: var(--om-text-body);'}
						onclick={() => actions.executeAction(card, action.action_id)}
						disabled={!!actions.executingActionId || isTerminal}
					>
						{#if actions.executingActionId === action.action_id}
							Executing…
						{:else}
							{#if isSelected}<span class="mr-1">&#10003;</span>{/if}
							{action.label}
							<span class="opacity-60">({action.target_platform})</span>
						{/if}
					</button>
				{/each}
			</div>
			{#if actions.executeError}
				<p class="om-entry-t mt-2" style="color: var(--om-alert-fg);">{actions.executeError}</p>
			{/if}
		{/if}
	</div>
{/if}

<OmniTooltip {tooltip} />
