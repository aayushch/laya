<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts" module>
	/**
	 * Shared portalled tooltip for the Omni board.
	 *
	 * Portalled, not inline: the board's columns are glass surfaces with
	 * `backdrop-filter`, which makes a containing block AND a stacking context —
	 * an inline tooltip is trapped inside its column and later rows paint over
	 * it. Same reason ListRow.svelte portals its own. z-[100] matches.
	 *
	 * Positioning flips to above/left near the viewport edge so a tooltip on the
	 * rightmost rail or the bottom row isn't clipped off-screen.
	 */
	export interface TooltipState {
		text: string;
		top: number;
		left: number;
		maxWidth: number;
	}

	const MARGIN = 8;
	// The tooltip takes its width from the element it explains, bounded at both
	// ends. It is the unclipped version of that row, so matching the row's width
	// makes it read as the same line continuing rather than a detached card — and
	// the aggregate sentences here are long enough that a fixed narrow box turns
	// two lines into a paragraph. The cap keeps the measure readable: unbounded,
	// a 200-character line would span the whole window in one unreadable run.
	const MIN_WIDTH = 260;
	const MAX_WIDTH = 620;

	/** Anchor a tooltip under an element, sized to it and kept on screen. */
	export function anchorTooltip(el: HTMLElement, text: string): TooltipState {
		const rect = el.getBoundingClientRect();
		const available = window.innerWidth - MARGIN * 2;
		const width = Math.min(available, Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, rect.width)));
		const left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - width - MARGIN));
		// Rough estimate is enough — we only need to know whether it would run off
		// the bottom, and flipping above the anchor is always safe when it would.
		const wouldOverflow = rect.bottom + 60 > window.innerHeight;
		const top = wouldOverflow ? Math.max(MARGIN, rect.top - 60) : rect.bottom + 4;
		return { text, top, left, maxWidth: width };
	}

	/**
	 * Anchor only when the element's text is actually clipped. Rows whose text
	 * already wraps in full (triage items, changelog entries) get no tooltip —
	 * repeating visible text is noise, not help.
	 */
	export function anchorIfTruncated(
		el: HTMLElement | undefined | null,
		text: string
	): TooltipState | null {
		if (!el || el.scrollWidth <= el.clientWidth) return null;
		return anchorTooltip(el, text);
	}
</script>

<script lang="ts">
	import { portal } from '$lib/actions/portal';

	let { tooltip }: { tooltip: TooltipState | null } = $props();
</script>

{#if tooltip}
	<span
		use:portal
		class="om-entry-t glass-tooltip pointer-events-none fixed z-[100] rounded-md border border-transparent px-2 py-1 font-medium break-words whitespace-normal"
		style="top: {tooltip.top}px; left: {tooltip.left}px; max-width: {tooltip.maxWidth}px;"
	>
		{tooltip.text}
	</span>
{/if}
