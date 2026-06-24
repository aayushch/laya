<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { invoke } from '@tauri-apps/api/core';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import type { Repo } from '$lib/api/types';

	interface RepoDetection {
		path: string;
		name: string;
		platform: string;
		remote_id: string;
		host: string;
	}

	let repos = $state<Repo[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let editingIndex = $state<number | null>(null);
	let showAddForm = $state(false);

	// Form fields
	let formName = $state('');
	let formPath = $state('');
	let formPlatform = $state('');
	let formRemoteId = $state('');
	let formHost = $state('');
	let detectionStatus = $state<{ ok: boolean; msg: string } | null>(null);
	let browsing = $state(false);

	let fixedTooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showTooltipIfTruncated(e: MouseEvent) {
		const el = e.currentTarget as HTMLElement;
		if (el.scrollWidth <= el.clientWidth) { fixedTooltip = null; return; }
		const rect = el.getBoundingClientRect();
		fixedTooltip = { text: el.textContent ?? '', top: rect.bottom + 4, left: rect.left };
	}

	function hideTooltip() { fixedTooltip = null; }

	onMount(async () => {
		try {
			const data = await engineApi.getRepos();
			repos = data.repos;
		} catch {
			error = 'Failed to load repository configuration';
		} finally {
			loading = false;
		}
	});

	async function save() {
		saving = true;
		error = null;
		try {
			await engineApi.updateRepos({ repos });
		} catch {
			error = 'Failed to save repos';
		} finally {
			saving = false;
		}
	}

	function resetForm() {
		formName = '';
		formPath = '';
		formPlatform = '';
		formRemoteId = '';
		formHost = '';
		showAddForm = false;
		editingIndex = null;
		detectionStatus = null;
	}

	async function browseRepo() {
		browsing = true;
		detectionStatus = null;
		try {
			const result = await invoke<RepoDetection>('pick_repo_folder');
			formPath = result.path;
			formName = result.name;
			formPlatform = result.platform;
			formRemoteId = result.remote_id;
			formHost = result.host;
			if (result.platform) {
				detectionStatus = { ok: true, msg: `${result.platform} · ${result.remote_id}` };
				// Known cloud host — auto-add when in add mode (not editing).
				if (editingIndex === null) {
					await addRepo();
				}
			} else {
				// Self-hosted/unknown host: we can't infer the platform (Bitbucket Server
				// vs GitLab vs GitHub Enterprise all look alike). Keep the form open with a
				// hint so the user picks the platform before adding.
				detectionStatus = {
					ok: true,
					msg: `Self-hosted host detected (${result.host}) — choose a platform, then Add`
				};
			}
		} catch (err: unknown) {
			const msg = String(err);
			if (!msg.includes('cancelled')) {
				detectionStatus = { ok: false, msg: msg.replace(/^Error: /, '') };
			}
		} finally {
			browsing = false;
		}
	}

	async function addRepo() {
		repos.push({ name: formName, path: formPath, platform: formPlatform, remote_id: formRemoteId, host: formHost });
		resetForm();
		await save();
	}

	function startEdit(index: number) {
		const r = repos[index];
		formName = r.name;
		formPath = r.path;
		formPlatform = r.platform;
		formRemoteId = r.remote_id;
		formHost = r.host ?? '';
		editingIndex = index;
		showAddForm = false;
	}

	async function saveEdit() {
		if (editingIndex !== null) {
			repos[editingIndex] = { name: formName, path: formPath, platform: formPlatform, remote_id: formRemoteId, host: formHost };
			resetForm();
			await save();
		}
	}

	async function removeRepo(index: number) {
		repos.splice(index, 1);
		await save();
	}
</script>

{#if loading}
	<div class="text-surface-400">Loading repos...</div>
{:else}
	{#if error}
		<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-laya-base text-red-300">{error}</div>
	{/if}

	<!-- Repo table -->
	<div class="overflow-hidden {$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700'}">
		<table class="w-full table-fixed text-laya-base">
			<thead class="{$glassTheme ? 'bg-white/[0.03]' : 'bg-surface-800'} text-left text-laya-secondary uppercase tracking-wider text-surface-400">
				<tr>
					<th class="w-[15%] px-4 py-3">Name</th>
					<th class="w-[25%] px-4 py-3">Path</th>
					<th class="w-[12%] px-4 py-3">Platform</th>
					<th class="px-4 py-3">Remote ID</th>
					<th class="w-[15%] px-4 py-3 text-right">Actions</th>
				</tr>
			</thead>
			<tbody class="divide-y {$glassTheme ? 'divide-white/[0.05]' : 'divide-surface-700'}">
				{#each repos as repo, i}
					<tr class="{$glassTheme ? 'bg-white/[0.02] hover:bg-white/[0.05]' : 'bg-surface-900 hover:bg-surface-800'}">
						<td class="truncate px-4 py-3 font-medium" onmouseenter={showTooltipIfTruncated} onmouseleave={hideTooltip}>{repo.name}</td>
						<td class="truncate px-4 py-3 font-mono text-laya-secondary text-surface-300" onmouseenter={showTooltipIfTruncated} onmouseleave={hideTooltip}>{repo.path}</td>
						<td class="px-4 py-3">
							{#if repo.platform}
								<span class="rounded-full bg-surface-700 px-2 py-0.5 text-laya-secondary">{repo.platform}</span>
							{:else}
								<span class="text-surface-500">-</span>
							{/if}
						</td>
						<td class="truncate px-4 py-3 text-surface-400" onmouseenter={showTooltipIfTruncated} onmouseleave={hideTooltip}>{repo.remote_id || '-'}</td>
						<td class="px-4 py-3 text-right whitespace-nowrap">
							<button class="text-surface-400 hover:text-surface-100" onclick={() => startEdit(i)}>Edit</button>
							<button class="ml-2 text-red-400 hover:text-red-300" onclick={() => removeRepo(i)}>Remove</button>
						</td>
					</tr>
				{/each}
				{#if repos.length === 0}
					<tr><td colspan="5" class="px-4 py-6 text-center text-surface-500">No repositories configured</td></tr>
				{/if}
			</tbody>
		</table>
	</div>

	<!-- Add/Edit form -->
	{#if showAddForm || editingIndex !== null}
		<div class="mt-4 {$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-4">
			<h3 class="mb-3 text-laya-base font-medium">{editingIndex !== null ? 'Edit Repository' : 'Add Repository'}</h3>
			<div class="mb-3 flex items-center gap-3">
				<button
					class="rounded-lg border border-surface-600 bg-surface-700 px-3 py-2 text-laya-base font-medium transition-colors hover:bg-surface-600 disabled:opacity-50"
					onclick={browseRepo}
					disabled={browsing}
				>
					{browsing ? 'Opening…' : 'Browse…'}
				</button>
				{#if detectionStatus}
					<span class="text-laya-base {detectionStatus.ok ? 'text-green-400' : 'text-red-400'}">
						{detectionStatus.ok ? '✓' : '✗'} {detectionStatus.msg}
					</span>
				{/if}
			</div>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<input bind:value={formName} placeholder="Name (e.g. payments-service)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
				<input bind:value={formPath} placeholder="Local path (e.g. /home/user/repos/payments)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
				<input bind:value={formPlatform} placeholder="Platform (e.g. github, bitbucket)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
				<input bind:value={formRemoteId} placeholder="Remote ID (e.g. org/repo)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
				<input bind:value={formHost} placeholder="Host (e.g. bitbucket.org; leave blank for cloud)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-laya-base text-surface-50 placeholder-surface-500" />
			</div>
			<div class="mt-3 flex gap-2">
				<button
					class="rounded-lg bg-surface-600 px-4 py-2 text-laya-base font-medium hover:bg-surface-500"
					onclick={editingIndex !== null ? saveEdit : addRepo}
					disabled={!formName || !formPath}
				>
					{saving ? 'Saving...' : editingIndex !== null ? 'Save' : 'Add'}
				</button>
				<button class="rounded-lg px-4 py-2 text-laya-base text-surface-400 hover:text-surface-200" onclick={resetForm}>
					Cancel
				</button>
			</div>
		</div>
	{:else}
		<button
			class="mt-4 rounded-lg border border-dashed border-surface-600 px-4 py-2 text-laya-base text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
			onclick={() => (showAddForm = true)}
		>
			+ Add Repository
		</button>
	{/if}
{/if}

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] max-w-sm rounded-md border border-transparent glass-tooltip px-2 py-1 text-laya-micro font-medium whitespace-normal break-all"
		style="top: {fixedTooltip.top}px; left: {fixedTooltip.left}px;"
	>
		{fixedTooltip.text}
	</span>
{/if}
