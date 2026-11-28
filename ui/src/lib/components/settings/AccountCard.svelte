<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { timeAgo, parseBackendDate } from '$lib/utils/datetime';
	import { STATUS_STYLE, statusTint } from '$lib/utils/integrationHealth';
	import TagInput from './TagInput.svelte';
	import type { EgressConnection } from '$lib/api/types';

	let {
		conn,
		platformKey,
		platformLabel,
		canReconnect,
		onReconnect,
		onRefresh
	}: {
		conn: EgressConnection;
		platformKey: string;
		platformLabel: string;
		/** OAuth platforms re-authorize in place. Credential platforms have no
		 *  Reconnect: a failing key is removed and added again. */
		canReconnect: boolean;
		onReconnect: (conn: EgressConnection) => void;
		onRefresh: () => void;
	} = $props();

	const status = $derived(STATUS_STYLE[conn.status]);
	const failing = $derived(conn.status !== 'connected');

	let testing = $state(false);
	let testResult = $state<{ valid: boolean; error?: string } | null>(null);
	let confirming = $state(false);
	let removing = $state(false);

	async function handleTest() {
		testing = true;
		testResult = null;
		try {
			const result = await engineApi.testEgressConnection(conn.connection_id);
			testResult = result;
			if (result.valid) {
				setTimeout(() => {
					if (testResult?.valid) testResult = null;
				}, 3000);
			}
			onRefresh();
		} catch (e) {
			testResult = { valid: false, error: e instanceof Error ? e.message : 'Test failed' };
		} finally {
			testing = false;
		}
	}

	async function handleRemove() {
		removing = true;
		try {
			await engineApi.deleteEgressConnection(conn.connection_id);
			confirming = false;
			onRefresh();
		} catch {
			// The card stays and the confirm strip remains open, so the user can retry.
		} finally {
			removing = false;
		}
	}

	// Slack channels and Trello boards: the list a connection monitors, shown
	// as chips and edited in place.
	const MONITORED = {
		slack: {
			label: 'Monitored channels',
			empty: 'No channels',
			note: 'Only these channels are ingested.',
			placeholder: 'add channel, Enter',
			prefix: '#',
			normalize: undefined,
			load: (id: string) => engineApi.getSlackChannels(id).then((r) => r.channels),
			save: (id: string, items: string[]) => engineApi.updateSlackChannels(id, items).then((r) => r.channels)
		},
		trello: {
			label: 'Monitored boards',
			empty: 'No boards',
			note: 'Only these boards are ingested.',
			placeholder: 'add board URL or ID, Enter',
			prefix: '',
			// Board short links are case-sensitive.
			normalize: (raw: string) => raw.trim(),
			load: (id: string) => engineApi.getTrelloBoards(id).then((r) => r.boards),
			save: (id: string, items: string[]) => engineApi.updateTrelloBoards(id, items).then((r) => r.boards)
		}
	};
	const monitored = $derived(
		platformKey === 'slack' || platformKey === 'trello' ? MONITORED[platformKey] : null
	);
	let monitoredItems = $state<string[]>([]);
	let editingMonitored = $state(false);
	let draftMonitored = $state<string[]>([]);
	let savingMonitored = $state(false);

	$effect(() => {
		if (!monitored) return;
		monitored
			.load(conn.connection_id)
			.then((items) => (monitoredItems = items || []))
			.catch(() => (monitoredItems = []));
	});

	function startEditMonitored() {
		draftMonitored = [...monitoredItems];
		editingMonitored = true;
	}

	async function saveMonitored() {
		if (!monitored || draftMonitored.length === 0) return;
		savingMonitored = true;
		try {
			const items = await monitored.save(conn.connection_id, draftMonitored);
			monitoredItems = items || draftMonitored;
			editingMonitored = false;
		} catch {
			// Edit mode stays open with the draft, so the user can retry.
		} finally {
			savingMonitored = false;
		}
	}

	function formatShortDate(s: string): string {
		const d = parseBackendDate(s);
		return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—';
	}

	const cardBorder = $derived(
		confirming
			? `1px solid ${statusTint('error', 0.35)}`
			: failing
				? `1px solid ${statusTint(conn.status, 0.35)}`
				: undefined
	);
</script>

<svelte:window
	onkeydown={(e) => {
		if (confirming && e.key === 'Escape') confirming = false;
	}}
/>

<div
	class="flex flex-col gap-3.5 rounded-[11px] bg-integ-card px-[18px] py-4 {cardBorder ? '' : 'border-[0.5px] border-integ-line'}"
	style:border={cardBorder}
>
	<!-- Top row: status, name, actions -->
	<div class="flex flex-wrap items-center gap-2.5 gap-y-2.5">
		<span
			class="h-2 w-2 shrink-0 rounded-full"
			style:background={status.dot}
			style:box-shadow={failing ? `0 0 0 3px ${statusTint(conn.status, 0.18)}` : undefined}
		></span>
		<span class="text-integ-name font-semibold text-integ-t1">{conn.name || platformLabel}</span>
		<span
			class="rounded-[5px] px-[7px] py-0.5 text-integ-label font-semibold tracking-[0.02em]"
			style:background={statusTint(conn.status, 0.14)}
			style:color={status.fg}
		>
			{status.label}
		</span>
		<span class="flex-1"></span>
		{#if !confirming}
			<div class="flex items-center gap-3.5">
				{#if failing && canReconnect}
					<button
						onclick={() => onReconnect(conn)}
						class="rounded-[7px] bg-laya-orange px-3 py-[5px] text-integ-body font-semibold text-integ-on-accent transition-colors hover:bg-laya-gold"
					>
						Reconnect
					</button>
				{/if}
				{#if testing}
					<span class="flex items-center gap-[7px] text-integ-body text-integ-t2">
						<span class="h-3 w-3 animate-spin rounded-full border-[1.5px] border-integ-line border-t-laya-orange"></span>
						Testing…
					</span>
				{:else}
					<button
						onclick={handleTest}
						class="text-integ-body text-integ-t3 transition-colors hover:text-integ-t1"
					>
						Test
					</button>
				{/if}
				<button
					onclick={() => (confirming = true)}
					class="text-integ-body text-integ-err-fg opacity-70 transition-opacity hover:opacity-100"
				>
					Remove
				</button>
			</div>
		{/if}
	</div>

	{#if testResult}
		{@const resultStatus = testResult.valid ? 'connected' : 'error'}
		<div
			class="flex items-center gap-2 rounded-[7px] px-3 py-2 text-integ-body"
			style:background={statusTint(resultStatus, 0.1)}
			style:color={STATUS_STYLE[resultStatus].fg}
			role="status"
		>
			<span class="font-bold">{testResult.valid ? '✓' : '!'}</span>
			<span>
				{testResult.valid
					? 'Connection valid'
					: `Still failing — ${testResult.error || conn.error_message || 'test failed'}`}
			</span>
		</div>
	{/if}

	<!-- Full error text, never truncated, selectable for copying. -->
	{#if failing && conn.error_message}
		<div
			class="select-text rounded-[7px] px-3 py-[9px] font-mono text-integ-note leading-[1.55] [overflow-wrap:anywhere]"
			style:background={statusTint(conn.status, 0.08)}
			style:color={status.fg}
		>
			{conn.error_message}
		</div>
	{/if}

	<div class="grid grid-cols-3 gap-3">
		<div class="flex flex-col gap-[3px]">
			<span class="text-integ-label uppercase tracking-[0.06em] text-integ-t4">
				{failing ? 'Last OK' : 'Last checked'}
			</span>
			<span class="font-mono text-integ-body text-integ-t2">
				{timeAgo(conn.last_validated_at, { nullLabel: '—' })}
			</span>
		</div>
		<div class="flex flex-col gap-[3px]">
			<span class="text-integ-label uppercase tracking-[0.06em] text-integ-t4">Connected</span>
			<span class="font-mono text-integ-body text-integ-t2">{formatShortDate(conn.created_at)}</span>
		</div>
		<div class="flex flex-col gap-[3px]">
			<span class="text-integ-label uppercase tracking-[0.06em] text-integ-t4">Actions</span>
			<span class="font-mono text-integ-body text-integ-t2">{conn.capabilities?.length ?? 0}</span>
		</div>
	</div>

	{#if monitored}
		<div class="flex flex-col gap-2 border-t-[0.5px] border-integ-line pt-3">
			<div class="flex items-center gap-2.5">
				<span class="text-integ-label uppercase tracking-[0.06em] text-integ-t4">{monitored.label}</span>
				<span class="flex-1"></span>
				{#if !editingMonitored}
					<button
						onclick={startEditMonitored}
						class="text-integ-body text-integ-t3 transition-colors hover:text-integ-t1"
					>
						Edit
					</button>
				{/if}
			</div>
			{#if editingMonitored}
				<TagInput
					bind:tags={draftMonitored}
					placeholder={monitored.placeholder}
					variant="accent"
					prefix={monitored.prefix}
					normalize={monitored.normalize}
				/>
				<div class="flex items-center gap-2">
					<button
						onclick={saveMonitored}
						disabled={savingMonitored || draftMonitored.length === 0}
						class="rounded-[7px] bg-laya-orange px-3 py-[5px] text-integ-body font-semibold text-integ-on-accent transition-colors hover:bg-laya-gold disabled:opacity-45 disabled:hover:bg-laya-orange"
					>
						{savingMonitored ? 'Saving…' : 'Save'}
					</button>
					<button
						onclick={() => (editingMonitored = false)}
						class="px-2.5 py-[5px] text-integ-body text-integ-t3 transition-colors hover:text-integ-t1"
					>
						Cancel
					</button>
					<span class="text-integ-small text-integ-t4">{monitored.note}</span>
				</div>
			{:else}
				<div class="flex flex-wrap gap-1.5">
					{#each monitoredItems as item (item)}
						<span class="rounded-[5px] bg-integ-fill px-2 py-[3px] font-mono text-integ-small text-integ-t2">{monitored.prefix}{item}</span>
					{:else}
						<span class="text-integ-small text-integ-t4">{monitored.empty}</span>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	{#if conn.capabilities?.length}
		<div class="flex flex-wrap gap-1.5">
			{#each conn.capabilities as cap (cap)}
				<span
					class="rounded-[5px] bg-integ-fill px-2 py-[3px] font-mono text-integ-chip {failing ? 'text-integ-t4' : 'text-integ-t2'}"
				>
					{cap}
				</span>
			{/each}
		</div>
	{/if}

	{#if confirming}
		<div class="flex flex-wrap items-center gap-3 rounded-lg bg-[rgb(239_68_68/0.08)] px-3.5 py-3">
			<div class="flex min-w-[220px] flex-1 flex-col gap-0.5">
				<span class="text-integ-item font-semibold text-integ-t1">Disconnect {conn.name || platformLabel}?</span>
				<span class="text-integ-note leading-normal text-integ-t3">
					Removes its credentials and deactivates the workflows it runs. Other {platformLabel} accounts keep working.
				</span>
			</div>
			<!-- svelte-ignore a11y_autofocus -->
			<button
				autofocus
				onclick={() => (confirming = false)}
				class="rounded-[7px] bg-integ-fill-2 px-3 py-1.5 text-integ-body text-integ-t2"
			>
				Cancel
			</button>
			<button
				onclick={handleRemove}
				disabled={removing}
				class="rounded-[7px] bg-[#dc2626] px-3 py-1.5 text-integ-body font-semibold text-white transition-colors hover:bg-[#ef4444] disabled:opacity-60"
			>
				{removing ? 'Disconnecting…' : 'Disconnect'}
			</button>
		</div>
	{/if}
</div>
