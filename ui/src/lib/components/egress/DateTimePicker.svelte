<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import { tick } from 'svelte';

	let {
		value = '',
		onchange,
		id = '',
	}: {
		value?: string;
		onchange?: (v: string) => void;
		id?: string;
	} = $props();

	let open = $state(false);
	let triggerEl = $state<HTMLButtonElement | undefined>();
	let popoverEl = $state<HTMLDivElement | undefined>();
	let pos = $state({ top: 0, left: 0 });

	let viewYear = $state(new Date().getFullYear());
	let viewMonth = $state(new Date().getMonth());

	let selectedYear = $state<number | null>(null);
	let selectedMonth = $state<number | null>(null);
	let selectedDay = $state<number | null>(null);
	let selectedHour = $state(9);
	let selectedMinute = $state(0);

	const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

	// Wheel refs
	let hourWheelEl = $state<HTMLDivElement | undefined>();
	let minuteWheelEl = $state<HTMLDivElement | undefined>();

	const ITEM_H = 28;
	const VISIBLE_ITEMS = 5;
	const WHEEL_H = ITEM_H * VISIBLE_ITEMS;
	const CENTER_OFFSET = ITEM_H * 2;

	const hours = Array.from({ length: 24 }, (_, i) => i);
	const minutes = Array.from({ length: 12 }, (_, i) => i * 5);

	function parseValue(v: string) {
		if (!v) return;
		const d = new Date(v);
		if (isNaN(d.getTime())) return;
		selectedYear = d.getFullYear();
		selectedMonth = d.getMonth();
		selectedDay = d.getDate();
		selectedHour = d.getHours();
		selectedMinute = d.getMinutes();
		viewYear = d.getFullYear();
		viewMonth = d.getMonth();
	}

	function emit() {
		if (selectedYear == null || selectedMonth == null || selectedDay == null) return;
		const y = String(selectedYear);
		const m = String(selectedMonth + 1).padStart(2, '0');
		const d = String(selectedDay).padStart(2, '0');
		const h = String(selectedHour).padStart(2, '0');
		const min = String(selectedMinute).padStart(2, '0');
		onchange?.(`${y}-${m}-${d}T${h}:${min}`);
	}

	function daysInMonth(year: number, month: number): number {
		return new Date(year, month + 1, 0).getDate();
	}

	function firstDayOfMonth(year: number, month: number): number {
		return new Date(year, month, 1).getDay();
	}

	const calendarDays = $derived.by(() => {
		const total = daysInMonth(viewYear, viewMonth);
		const offset = firstDayOfMonth(viewYear, viewMonth);
		const days: (number | null)[] = [];
		for (let i = 0; i < offset; i++) days.push(null);
		for (let i = 1; i <= total; i++) days.push(i);
		return days;
	});

	const today = new Date();
	const todayYear = today.getFullYear();
	const todayMonth = today.getMonth();
	const todayDay = today.getDate();

	function prevMonth() {
		if (viewMonth === 0) { viewYear--; viewMonth = 11; }
		else viewMonth--;
	}

	function nextMonth() {
		if (viewMonth === 11) { viewYear++; viewMonth = 0; }
		else viewMonth++;
	}

	function selectDay(day: number) {
		selectedYear = viewYear;
		selectedMonth = viewMonth;
		selectedDay = day;
		emit();
	}

	function scrollWheelTo(el: HTMLDivElement | undefined, index: number) {
		if (!el) return;
		el.scrollTo({ top: index * ITEM_H, behavior: 'smooth' });
	}

	function handleHourScroll() {
		if (!hourWheelEl) return;
		const idx = Math.round(hourWheelEl.scrollTop / ITEM_H);
		const clamped = Math.max(0, Math.min(idx, hours.length - 1));
		if (selectedHour !== hours[clamped]) {
			selectedHour = hours[clamped];
			emit();
		}
	}

	function handleMinuteScroll() {
		if (!minuteWheelEl) return;
		const idx = Math.round(minuteWheelEl.scrollTop / ITEM_H);
		const clamped = Math.max(0, Math.min(idx, minutes.length - 1));
		if (selectedMinute !== minutes[clamped]) {
			selectedMinute = minutes[clamped];
			emit();
		}
	}

	let hourScrollTimer: ReturnType<typeof setTimeout> | null = null;
	let minuteScrollTimer: ReturnType<typeof setTimeout> | null = null;

	function onHourScroll() {
		if (hourScrollTimer) clearTimeout(hourScrollTimer);
		hourScrollTimer = setTimeout(() => {
			handleHourScroll();
			// Snap to nearest
			if (hourWheelEl) {
				const idx = Math.round(hourWheelEl.scrollTop / ITEM_H);
				hourWheelEl.scrollTo({ top: idx * ITEM_H, behavior: 'smooth' });
			}
		}, 80);
	}

	function onMinuteScroll() {
		if (minuteScrollTimer) clearTimeout(minuteScrollTimer);
		minuteScrollTimer = setTimeout(() => {
			handleMinuteScroll();
			if (minuteWheelEl) {
				const idx = Math.round(minuteWheelEl.scrollTop / ITEM_H);
				minuteWheelEl.scrollTo({ top: idx * ITEM_H, behavior: 'smooth' });
			}
		}, 80);
	}

	function clickHour(h: number) {
		selectedHour = h;
		emit();
		scrollWheelTo(hourWheelEl, hours.indexOf(h));
	}

	function clickMinute(m: number) {
		selectedMinute = m;
		emit();
		scrollWheelTo(minuteWheelEl, minutes.indexOf(m));
	}

	async function initWheels() {
		await tick();
		if (hourWheelEl) hourWheelEl.scrollTop = hours.indexOf(selectedHour) * ITEM_H;
		if (minuteWheelEl) {
			const mIdx = minutes.indexOf(selectedMinute);
			minuteWheelEl.scrollTop = (mIdx >= 0 ? mIdx : 0) * ITEM_H;
		}
	}

	async function toggleOpen() {
		if (open) { open = false; return; }
		if (value) parseValue(value);
		open = true;
		await tick();
		reposition();
		initWheels();
	}

	function reposition() {
		if (!triggerEl) return;
		const r = triggerEl.getBoundingClientRect();
		let top = r.bottom + 6;
		let left = r.left;
		if (top + 480 > window.innerHeight) top = r.top - 480 - 6;
		if (left + 300 > window.innerWidth) left = window.innerWidth - 308;
		pos = { top, left };
	}

	function handleClickOutside(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (triggerEl?.contains(target) || popoverEl?.contains(target)) return;
		open = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); open = false; }
	}

	$effect(() => {
		if (!open) return;
		window.addEventListener('mousedown', handleClickOutside, true);
		window.addEventListener('keydown', handleKeydown, true);
		return () => {
			window.removeEventListener('mousedown', handleClickOutside, true);
			window.removeEventListener('keydown', handleKeydown, true);
		};
	});

	const displayValue = $derived.by(() => {
		if (!value) return '';
		const d = new Date(value);
		if (isNaN(d.getTime())) return value;
		const mon = MONTHS[d.getMonth()];
		const h = d.getHours();
		const h12 = h % 12 || 12;
		return `${mon} ${d.getDate()}, ${d.getFullYear()}  ${h12}:${String(d.getMinutes()).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
	});

	const inputBase = $derived($glassTheme
		? 'border-surface-600/40 bg-surface-800/40 backdrop-blur-sm'
		: 'border-surface-600 bg-surface-800');

	const popoverClass = $derived($glassTheme
		? 'glass-card border-white/[0.12]'
		: 'border-surface-600 bg-surface-800 shadow-2xl');
</script>

<button
	bind:this={triggerEl}
	type="button"
	{id}
	class="w-full rounded-md border {inputBase} px-3 py-2 text-sm text-left text-surface-200 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30 transition-colors"
	onclick={toggleOpen}
>
	{#if displayValue}
		{displayValue}
	{:else}
		<span class="text-surface-600">Pick date & time</span>
	{/if}
</button>

{#if open}
	<div
		bind:this={popoverEl}
		use:portal
		class="fixed z-[100] w-[300px] rounded-xl border p-3 {popoverClass}"
		style="top: {pos.top}px; left: {pos.left}px;"
	>
		<!-- Month nav -->
		<div class="flex items-center justify-between mb-2">
			<button type="button" class="rounded p-1 text-surface-400 hover:text-surface-200 transition-colors {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}" onclick={prevMonth} aria-label="Previous month">
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
			</button>
			<span class="text-xs font-semibold text-surface-200">{MONTHS[viewMonth]} {viewYear}</span>
			<button type="button" class="rounded p-1 text-surface-400 hover:text-surface-200 transition-colors {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}" onclick={nextMonth} aria-label="Next month">
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
			</button>
		</div>

		<!-- Weekday headers -->
		<div class="grid grid-cols-7 mb-1">
			{#each WEEKDAYS as wd}
				<div class="text-center text-[10px] font-medium text-surface-500 py-0.5">{wd}</div>
			{/each}
		</div>

		<!-- Day grid -->
		<div class="grid grid-cols-7 gap-px">
			{#each calendarDays as day}
				{#if day === null}
					<div></div>
				{:else}
					{@const sel = day === selectedDay && viewMonth === selectedMonth && viewYear === selectedYear}
					{@const td = day === todayDay && viewMonth === todayMonth && viewYear === todayYear}
					<button
						type="button"
						class="h-7 w-full rounded-md text-xs font-medium transition-colors
							{sel ? 'bg-laya-orange text-surface-950'
								: td ? ($glassTheme ? 'bg-white/[0.08] text-laya-orange' : 'bg-surface-700 text-laya-orange')
								: ($glassTheme ? 'text-surface-300 hover:bg-white/[0.08]' : 'text-surface-300 hover:bg-surface-700')}"
						onclick={() => selectDay(day)}
					>{day}</button>
				{/if}
			{/each}
		</div>

		<div class="my-2.5 border-t {$glassTheme ? 'border-white/[0.08]' : 'border-surface-700'}"></div>

		<!-- Drum time picker -->
		<div class="relative" style="height: {WHEEL_H}px;">
			<!-- Selection band -->
			<div
				class="pointer-events-none absolute left-0 right-0 rounded-lg {$glassTheme ? 'bg-white/[0.08]' : 'bg-surface-700/60'}"
				style="top: {CENTER_OFFSET}px; height: {ITEM_H}px;"
			></div>

			<!-- Fade masks -->
			<div class="pointer-events-none absolute inset-x-0 top-0 z-10 rounded-t-lg" style="height: {CENTER_OFFSET}px; background: linear-gradient(to bottom, {$glassTheme ? 'rgba(30,28,25,0.85)' : 'rgba(24,24,27,0.9)'}, transparent);"></div>
			<div class="pointer-events-none absolute inset-x-0 bottom-0 z-10 rounded-b-lg" style="height: {CENTER_OFFSET}px; background: linear-gradient(to top, {$glassTheme ? 'rgba(30,28,25,0.85)' : 'rgba(24,24,27,0.9)'}, transparent);"></div>

			<!-- Columns -->
			<div class="absolute inset-0 flex">
				<!-- Hours -->
				<div
					bind:this={hourWheelEl}
					class="flex-1 overflow-y-auto scrollbar-none snap-y snap-mandatory"
					style="scroll-padding-top: {CENTER_OFFSET}px; -ms-overflow-style: none; scrollbar-width: none;"
					onscroll={onHourScroll}
				>
					<div style="height: {CENTER_OFFSET}px;"></div>
					{#each hours as h}
						<button
							type="button"
							class="snap-start flex w-full items-center justify-center font-mono transition-colors {h === selectedHour ? 'text-surface-50 text-sm font-semibold' : 'text-surface-500 text-xs'}"
							style="height: {ITEM_H}px;"
							onclick={() => clickHour(h)}
						>{String(h).padStart(2, '0')}</button>
					{/each}
					<div style="height: {CENTER_OFFSET}px;"></div>
				</div>

				<!-- Separator -->
				<div class="flex items-center justify-center shrink-0 w-4">
					<span class="text-surface-500 font-mono text-sm font-semibold">:</span>
				</div>

				<!-- Minutes -->
				<div
					bind:this={minuteWheelEl}
					class="flex-1 overflow-y-auto scrollbar-none snap-y snap-mandatory"
					style="scroll-padding-top: {CENTER_OFFSET}px; -ms-overflow-style: none; scrollbar-width: none;"
					onscroll={onMinuteScroll}
				>
					<div style="height: {CENTER_OFFSET}px;"></div>
					{#each minutes as m}
						<button
							type="button"
							class="snap-start flex w-full items-center justify-center font-mono transition-colors {m === selectedMinute ? 'text-surface-50 text-sm font-semibold' : 'text-surface-500 text-xs'}"
							style="height: {ITEM_H}px;"
							onclick={() => clickMinute(m)}
						>{String(m).padStart(2, '0')}</button>
					{/each}
					<div style="height: {CENTER_OFFSET}px;"></div>
				</div>
			</div>
		</div>
	</div>
{/if}
