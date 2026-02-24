<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { TeamMember } from '$lib/api/types';

	const roles: TeamMember['role'][] = ['manager', 'teammate', 'external', 'bot'];

	let members = $state<TeamMember[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let editingIndex = $state<number | null>(null);
	let showAddForm = $state(false);

	// Form fields
	let formName = $state('');
	let formEmail = $state('');
	let formRole = $state<TeamMember['role']>('teammate');
	let formNotes = $state('');

	onMount(async () => {
		try {
			const data = await engineApi.getTeam();
			members = data.members;
		} catch {
			error = 'Failed to load team configuration';
		} finally {
			loading = false;
		}
	});

	async function save() {
		saving = true;
		error = null;
		try {
			await engineApi.updateTeam({ members });
		} catch {
			error = 'Failed to save team';
		} finally {
			saving = false;
		}
	}

	function resetForm() {
		formName = '';
		formEmail = '';
		formRole = 'teammate';
		formNotes = '';
		showAddForm = false;
		editingIndex = null;
	}

	async function addMember() {
		members.push({ name: formName, email: formEmail, role: formRole, notes: formNotes });
		resetForm();
		await save();
	}

	function startEdit(index: number) {
		const m = members[index];
		formName = m.name;
		formEmail = m.email;
		formRole = m.role;
		formNotes = m.notes;
		editingIndex = index;
		showAddForm = false;
	}

	async function saveEdit() {
		if (editingIndex !== null) {
			members[editingIndex] = { name: formName, email: formEmail, role: formRole, notes: formNotes };
			resetForm();
			await save();
		}
	}

	async function removeMember(index: number) {
		members.splice(index, 1);
		await save();
	}
</script>

{#if loading}
	<div class="text-surface-400">Loading team...</div>
{:else}
	{#if error}
		<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">{error}</div>
	{/if}

	<!-- Member table -->
	<div class="overflow-hidden rounded-xl border border-surface-700">
		<table class="w-full text-sm">
			<thead class="bg-surface-800 text-left text-xs uppercase tracking-wider text-surface-400">
				<tr>
					<th class="px-4 py-3">Name</th>
					<th class="px-4 py-3">Email</th>
					<th class="px-4 py-3">Role</th>
					<th class="px-4 py-3">Notes</th>
					<th class="px-4 py-3 text-right">Actions</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-surface-700">
				{#each members as member, i}
					<tr class="bg-surface-900 hover:bg-surface-800">
						<td class="px-4 py-3 font-medium">{member.name}</td>
						<td class="px-4 py-3 text-surface-300">{member.email}</td>
						<td class="px-4 py-3">
							<span class="rounded-full bg-surface-700 px-2 py-0.5 text-xs">{member.role}</span>
						</td>
						<td class="px-4 py-3 text-surface-400">{member.notes}</td>
						<td class="px-4 py-3 text-right">
							<button class="text-surface-400 hover:text-surface-100" onclick={() => startEdit(i)}>Edit</button>
							<button class="ml-2 text-red-400 hover:text-red-300" onclick={() => removeMember(i)}>Remove</button>
						</td>
					</tr>
				{/each}
				{#if members.length === 0}
					<tr><td colspan="5" class="px-4 py-6 text-center text-surface-500">No team members configured</td></tr>
				{/if}
			</tbody>
		</table>
	</div>

	<!-- Add/Edit form -->
	{#if showAddForm || editingIndex !== null}
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-4">
			<h3 class="mb-3 text-sm font-medium">{editingIndex !== null ? 'Edit Member' : 'Add Member'}</h3>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<input bind:value={formName} placeholder="Name" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
				<input bind:value={formEmail} placeholder="Email" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
				<select bind:value={formRole} class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50">
					{#each roles as role}
						<option value={role}>{role}</option>
					{/each}
				</select>
				<input bind:value={formNotes} placeholder="Notes (optional)" class="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-surface-50 placeholder-surface-500" />
			</div>
			<div class="mt-3 flex gap-2">
				<button
					class="rounded-lg bg-surface-600 px-4 py-2 text-sm font-medium hover:bg-surface-500"
					onclick={editingIndex !== null ? saveEdit : addMember}
					disabled={!formName || !formEmail}
				>
					{saving ? 'Saving...' : editingIndex !== null ? 'Save' : 'Add'}
				</button>
				<button class="rounded-lg px-4 py-2 text-sm text-surface-400 hover:text-surface-200" onclick={resetForm}>
					Cancel
				</button>
			</div>
		</div>
	{:else}
		<button
			class="rounded-lg border border-dashed border-surface-600 px-4 py-2 text-sm text-surface-400 transition-colors hover:border-surface-400 hover:text-surface-200"
			onclick={() => (showAddForm = true)}
		>
			+ Add Member
		</button>
	{/if}
{/if}
