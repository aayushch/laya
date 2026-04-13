<script lang="ts">
	import { agentDialog } from '$lib/stores/agentDialog';
	import { engineApi } from '$lib/api/engine';
	import { goto } from '$app/navigation';

	const agents = [
		{ value: 'claude_code', label: 'Claude Code', modes: ['plan', 'acceptEdits'] },
		{ value: 'gemini_cli', label: 'Gemini CLI', modes: [] },
		{ value: 'codex_cli', label: 'Codex CLI', modes: ['read-only', 'full-auto'] }
	];

	const modeLabels: Record<string, string> = {
		plan: 'Plan',
		acceptEdits: 'Accept Edits',
		'read-only': 'Read Only',
		'full-auto': 'Full Auto'
	};

	const modeDescriptions: Record<string, string> = {
		plan: 'Agent creates a plan and asks for approval before making changes',
		acceptEdits: 'Agent can read and write files without asking',
		'read-only': 'Agent can only read files, sandbox mode',
		'full-auto': 'Agent can read and write files automatically'
	};

	interface UploadedImage {
		path: string;
		filename: string;
		previewUrl: string;
	}

	import type { Repo } from '$lib/api/types';

	let selectedAgent = $state('claude_code');
	let selectedMode = $state('plan');
	let directory = $state('');
	let addDirs = $state<string[]>([]);
	let prompt = $state('');
	let images = $state<UploadedImage[]>([]);
	let submitting = $state(false);
	let uploading = $state(false);
	let error = $state<string | null>(null);
	let settingsLoaded = $state(false);
	let configuredAgent = $state<string | null>(null);
	let agentPaths = $state<Record<string, string>>({});
	let dragOver = $state(false);
	let repos = $state<Repo[]>([]);
	let dirDropdownOpen = $state(false);

	const currentAgent = $derived(agents.find((a) => a.value === selectedAgent));
	const availableModes = $derived(currentAgent?.modes ?? []);
	const hasMultipleModes = $derived(availableModes.length > 1);

	// Load settings to get configured agent and paths
	$effect(() => {
		if ($agentDialog.isOpen && !settingsLoaded) {
			loadSettings();
		}
	});

	// Reset mode when agent changes
	$effect(() => {
		const agent = agents.find((a) => a.value === selectedAgent);
		if (agent && agent.modes.length > 0) {
			if (!agent.modes.includes(selectedMode)) {
				selectedMode = agent.modes[0];
			}
		}
	});

	async function loadSettings() {
		try {
			const [settings, reposConfig] = await Promise.all([
				engineApi.getSettings(),
				engineApi.getRepos()
			]);
			const agent = settings.coding_agent || 'claude_code';
			if (agent !== 'none') {
				configuredAgent = agent;
				selectedAgent = agent;
			}
			agentPaths = settings.agent_paths || {};
			repos = reposConfig.repos || [];
			settingsLoaded = true;
		} catch {
			settingsLoaded = true;
		}
	}

	// --- Browse directory using Tauri dialog (graceful fallback for web dev) ---

	async function browseAddDir() {
		try {
			const { invoke } = await import('@tauri-apps/api/core');
			const path = await invoke<string>('pick_folder', {
				title: 'Select additional directory'
			});
			if (path && !addDirs.includes(path)) {
				addDirs = [...addDirs, path];
			}
		} catch {
			// Not in Tauri — ignore
		}
	}

	function removeAddDir(index: number) {
		addDirs = addDirs.filter((_, i) => i !== index);
	}

	// --- Image upload/handling ---

	async function uploadImageFile(file: File) {
		if (!file.type.startsWith('image/')) return;

		uploading = true;
		error = null;
		try {
			const formData = new FormData();
			formData.append('file', file);

			const resp = await fetch('http://127.0.0.1:8420/upload-agent-image', {
				method: 'POST',
				body: formData
			});
			if (!resp.ok) {
				throw new Error(`Upload failed: ${resp.status}`);
			}
			const result = await resp.json();

			// Create a local preview URL
			const previewUrl = URL.createObjectURL(file);
			images = [
				...images,
				{ path: result.path, filename: result.filename, previewUrl }
			];
		} catch (err) {
			error = err instanceof Error ? err.message : 'Image upload failed';
		} finally {
			uploading = false;
		}
	}

	function removeImage(index: number) {
		const removed = images[index];
		if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
		images = images.filter((_, i) => i !== index);
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		dragOver = false;
		const files = e.dataTransfer?.files;
		if (files) {
			for (const file of files) {
				if (file.type.startsWith('image/')) {
					uploadImageFile(file);
				}
			}
		}
	}

	function handleDragOver(e: DragEvent) {
		e.preventDefault();
		dragOver = true;
	}

	function handleDragLeave() {
		dragOver = false;
	}

	function handlePaste(e: ClipboardEvent) {
		const items = e.clipboardData?.items;
		if (!items) return;
		for (const item of items) {
			if (item.type.startsWith('image/')) {
				e.preventDefault();
				const file = item.getAsFile();
				if (file) uploadImageFile(file);
			}
		}
	}

	// --- Submit ---

	async function submit() {
		if (!prompt.trim()) {
			error = 'Please enter a prompt';
			return;
		}
		if (!directory.trim()) {
			error = 'Please specify a directory';
			return;
		}

		submitting = true;
		error = null;

		try {
			const result = await engineApi.runAgent({
				prompt: prompt.trim(),
				directory: directory.trim(),
				add_dirs: addDirs.length > 0 ? addDirs : undefined,
				agent_type: selectedAgent,
				mode: availableModes.length > 0 ? selectedMode : undefined,
				images: images.length > 0 ? images.map((i) => i.path) : undefined
			});

			agentDialog.close();
			resetState();
			goto(`/workspace/${result.card_id}`);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to start agent';
		} finally {
			submitting = false;
		}
	}

	function resetState() {
		prompt = '';
		directory = '';
		addDirs = [];
		// Revoke all preview URLs
		for (const img of images) {
			if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
		}
		images = [];
		error = null;
		submitting = false;
		uploading = false;
		settingsLoaded = false;
		dragOver = false;
		dirDropdownOpen = false;
	}

	function close() {
		agentDialog.close();
		resetState();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
		if (e.key === 'Enter' && e.metaKey) submit();
	}

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) close();
	}

	// Close directory dropdown when clicking anywhere else in the modal
	function handleModalClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (dirDropdownOpen && !target.closest('.relative')) {
			dirDropdownOpen = false;
		}
	}

	const inputClass =
		'w-full rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30';
	const labelClass = 'block text-xs font-medium text-surface-400 mb-1';
</script>

{#if $agentDialog.isOpen}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Run Agent"
		tabindex="0"
		onclick={handleBackdrop}
		onkeydown={handleKeydown}
	>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="mx-4 w-full max-w-2xl rounded-xl border border-surface-700 bg-surface-900 shadow-2xl max-h-[90vh] flex flex-col"
			onclick={handleModalClick}
		>
			<!-- Header -->
			<div
				class="flex items-center justify-between border-b border-surface-700 px-5 py-3 shrink-0"
			>
				<div class="flex items-center gap-2">
					<svg
						class="h-4 w-4 text-laya-orange"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
						/>
					</svg>
					<h2 class="text-sm font-semibold text-surface-50">Run Agent</h2>
				</div>
				<button
					class="rounded p-1 text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					aria-label="Close"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M6 18L18 6M6 6l12 12"
						/>
					</svg>
				</button>
			</div>

			<!-- Scrollable form body -->
			<div class="space-y-4 overflow-y-auto p-5">
				<!-- Agent selector -->
				<div>
					<span class={labelClass}>Agent</span>
					<div class="flex gap-2">
						{#each agents as agent}
							<button
								class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors
									{selectedAgent === agent.value
									? 'border-laya-orange bg-laya-orange/10'
									: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
								onclick={() => (selectedAgent = agent.value)}
							>
								<div class="text-xs font-medium">{agent.label}</div>
								{#if agentPaths[agent.value]}
									<div class="mt-0.5 text-[10px] text-green-400/70">configured</div>
								{:else}
									<div class="mt-0.5 text-[10px] text-surface-500">not found</div>
								{/if}
							</button>
						{/each}
					</div>
				</div>

				<!-- Mode selector (only shown when agent has modes) -->
				{#if hasMultipleModes}
					<div>
						<span class={labelClass}>Mode</span>
						<div class="flex gap-2">
							{#each availableModes as mode}
								<button
									class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors
										{selectedMode === mode
										? 'border-laya-orange bg-laya-orange/10'
										: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
									onclick={() => (selectedMode = mode)}
								>
									<div class="text-xs font-medium">{modeLabels[mode] || mode}</div>
									<div class="mt-0.5 text-[10px] text-surface-400">
										{modeDescriptions[mode] || ''}
									</div>
								</button>
							{/each}
						</div>
					</div>
				{/if}

				<!-- Working Directory -->
				<div class="relative">
					<label class={labelClass} for="agent-directory">Working Directory</label>
					<button
						id="agent-directory"
						type="button"
						class="flex w-full items-center justify-between rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-left text-sm transition-colors hover:border-surface-500 {directory ? 'text-surface-200' : 'text-surface-500'}"
						onclick={() => (dirDropdownOpen = !dirDropdownOpen)}
					>
						<span class="truncate">{directory || 'Select a repository...'}</span>
						<svg
							class="ml-2 h-4 w-4 shrink-0 text-surface-400 transition-transform {dirDropdownOpen ? 'rotate-180' : ''}"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
						</svg>
					</button>

					{#if dirDropdownOpen}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="absolute z-50 mt-1 w-full rounded-md border border-surface-600 bg-surface-800 shadow-lg"
							onkeydown={(e) => { if (e.key === 'Escape') dirDropdownOpen = false; }}
						>
							<div class="max-h-48 overflow-y-auto py-1">
								{#each repos as repo}
									<button
										class="flex w-full flex-col px-3 py-2 text-left transition-colors hover:bg-surface-700 {directory === repo.path ? 'bg-surface-700' : ''}"
										onclick={() => { directory = repo.path; dirDropdownOpen = false; }}
									>
										<span class="text-xs font-medium text-surface-200">{repo.name}</span>
										<span class="truncate text-[10px] text-surface-500">{repo.path}</span>
									</button>
								{/each}
								{#if repos.length === 0}
									<div class="px-3 py-2 text-xs text-surface-500">No repos configured</div>
								{/if}
							</div>
						</div>
					{/if}
				</div>

				<!-- Additional directories -->
				<div>
					<label class={labelClass} for="agent-add-dirs-input">
						Additional Directories
						<span class="font-normal text-surface-500">(optional)</span>
					</label>
					{#if addDirs.length > 0}
						<div class="mb-2 space-y-1">
							{#each addDirs as dir, i}
								<div
									class="flex items-center gap-2 rounded-md border border-surface-700 bg-surface-800 px-3 py-1.5"
								>
									<span class="flex-1 truncate font-mono text-xs text-surface-300"
										>{dir}</span
									>
									<button
										class="shrink-0 text-surface-500 transition-colors hover:text-red-400"
										onclick={() => removeAddDir(i)}
										aria-label="Remove directory"
									>
										<svg
											class="h-3.5 w-3.5"
											fill="none"
											stroke="currentColor"
											viewBox="0 0 24 24"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d="M6 18L18 6M6 6l12 12"
											/>
										</svg>
									</button>
								</div>
							{/each}
						</div>
					{/if}
					<button
						class="inline-flex items-center gap-1.5 rounded-md border border-dashed border-surface-600 px-3 py-1.5 text-xs text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-300"
						onclick={browseAddDir}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 4v16m8-8H4"
							/>
						</svg>
						Add directory
					</button>
				</div>

				<!-- Prompt + image drop zone -->
				<div>
					<label class={labelClass} for="agent-prompt">Prompt</label>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="relative"
						ondrop={handleDrop}
						ondragover={handleDragOver}
						ondragleave={handleDragLeave}
					>
						<textarea
							id="agent-prompt"
							bind:value={prompt}
							rows="6"
							class="{inputClass} resize-y {dragOver ? '!border-laya-orange !bg-laya-orange/5' : ''}"
							placeholder="Describe what you want the agent to do...&#10;&#10;Drop or paste images here for reference"
							onpaste={handlePaste}
						></textarea>

						{#if dragOver}
							<div
								class="pointer-events-none absolute inset-0 flex items-center justify-center rounded-md"
							>
								<div class="flex items-center gap-2 text-sm text-laya-orange">
									<svg
										class="h-5 w-5"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
										/>
									</svg>
									Drop image here
								</div>
							</div>
						{/if}
					</div>

					<!-- Image previews -->
					{#if images.length > 0}
						<div class="mt-2 flex flex-wrap gap-2">
							{#each images as img, i}
								<div class="group relative">
									<img
										src={img.previewUrl}
										alt={img.filename}
										class="h-16 w-16 rounded-md border border-surface-700 object-cover"
									/>
									<button
										class="absolute -right-1.5 -top-1.5 hidden rounded-full bg-surface-800 p-0.5 text-surface-400 shadow-md transition-colors hover:text-red-400 group-hover:block"
										onclick={() => removeImage(i)}
										aria-label="Remove image"
									>
										<svg
											class="h-3 w-3"
											fill="none"
											stroke="currentColor"
											viewBox="0 0 24 24"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d="M6 18L18 6M6 6l12 12"
											/>
										</svg>
									</button>
									<div
										class="absolute bottom-0 left-0 right-0 truncate rounded-b-md bg-black/60 px-1 py-0.5 text-[9px] text-surface-300"
									>
										{img.filename}
									</div>
								</div>
							{/each}
						</div>
					{/if}

					{#if uploading}
						<div class="mt-1 flex items-center gap-1.5 text-[10px] text-surface-400">
							<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle
									class="opacity-25"
									cx="12"
									cy="12"
									r="10"
									stroke="currentColor"
									stroke-width="4"
								></circle>
								<path
									class="opacity-75"
									fill="currentColor"
									d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
								></path>
							</svg>
							Uploading image...
						</div>
					{/if}

					<p class="mt-1 text-[10px] text-surface-500">
						<kbd
							class="rounded border border-surface-600 bg-surface-800 px-1 py-0.5 font-mono text-[10px]"
							>Cmd+Enter</kbd
						> to submit &middot; Drop or paste images for reference
					</p>
				</div>

				{#if error}
					<p class="text-xs text-red-400">{error}</p>
				{/if}
			</div>

			<!-- Footer -->
			<div
				class="flex items-center justify-end gap-2 border-t border-surface-700 px-5 py-3 shrink-0"
			>
				<button
					class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					disabled={submitting}
				>
					Cancel
				</button>
				<button
					class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-4 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:cursor-not-allowed disabled:opacity-50"
					onclick={submit}
					disabled={submitting || !prompt.trim() || !directory.trim()}
				>
					{#if submitting}
						<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
							<circle
								class="opacity-25"
								cx="12"
								cy="12"
								r="10"
								stroke="currentColor"
								stroke-width="4"
							></circle>
							<path
								class="opacity-75"
								fill="currentColor"
								d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
							></path>
						</svg>
						Starting...
					{:else}
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"
							/>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
							/>
						</svg>
						Run Agent
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
