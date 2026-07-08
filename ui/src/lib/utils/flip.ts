// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared FLIP (First-Last-Invert-Play) animation primitive (P7-7).
 *
 * feed/+page.svelte hand-rolled the same capture → invert → play loop twice —
 * once for the feed re-sort / column repack (xy, entrance-animated) and once for
 * the recent-drawer reorder (y-only). This centralises the mechanics; callers
 * supply the container, how to key each element, and the axis/duration/entrance
 * differences. Timing is preserved exactly from the originals.
 */
import { tick } from 'svelte';

/** Snapshot the on-screen rects of the tracked elements, keyed by their id. */
export function capturePositions(
	container: HTMLElement | null,
	selector: string,
	idOf: (el: HTMLElement) => string | undefined
): Map<string, DOMRect> {
	const positions = new Map<string, DOMRect>();
	if (!container) return positions;
	container.querySelectorAll<HTMLElement>(selector).forEach((el) => {
		const id = idOf(el);
		if (id) positions.set(id, el.getBoundingClientRect());
	});
	return positions;
}

export interface FlipOptions {
	/** Which axes to translate. The recent drawer only moves vertically. */
	axis?: 'y' | 'xy';
	/** Transform transition duration (ms). */
	durationMs?: number;
	/** Fade+scale brand-new elements (no prior position) in. */
	animateEntrance?: boolean;
	/** Entrance transition duration (ms). */
	entranceDurationMs?: number;
}

/**
 * Play the FLIP from `oldPositions` to the elements' current positions. Awaits a
 * tick first so the DOM has settled into its new layout before measuring, then
 * inverts each moved element to its old spot and transitions it back to 0.
 *
 * This does NOT itself gate a "settled" promise — callers that need one (the
 * feed's `_flipSettled`, used to defer scroll-into-view) own that timer, since
 * only they know whether the run was skipped for reduced-motion / instant mode.
 */
export async function playFlip(
	container: HTMLElement | null,
	selector: string,
	idOf: (el: HTMLElement) => string | undefined,
	oldPositions: Map<string, DOMRect>,
	opts: FlipOptions = {}
): Promise<void> {
	const {
		axis = 'xy',
		durationMs = 300,
		animateEntrance = false,
		entranceDurationMs = 250,
	} = opts;
	if (!container || oldPositions.size === 0) return;
	await tick();
	container.querySelectorAll<HTMLElement>(selector).forEach((el) => {
		const id = idOf(el);
		if (!id) return;
		const oldRect = oldPositions.get(id);
		if (!oldRect) {
			// Brand-new element — fade + scale it in (feed only).
			if (!animateEntrance) return;
			el.style.opacity = '0';
			el.style.transform = 'scale(0.95)';
			el.style.transition = 'none';
			requestAnimationFrame(() => {
				el.style.transition = `opacity ${entranceDurationMs}ms ease, transform ${entranceDurationMs}ms ease`;
				el.style.opacity = '';
				el.style.transform = '';
				el.addEventListener('transitionend', () => { el.style.transition = ''; }, { once: true });
			});
			return;
		}
		const newRect = el.getBoundingClientRect();
		const dx = axis === 'xy' ? oldRect.left - newRect.left : 0;
		const dy = oldRect.top - newRect.top;
		if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
		el.style.transform = axis === 'xy' ? `translate(${dx}px, ${dy}px)` : `translateY(${dy}px)`;
		el.style.transition = 'none';
		requestAnimationFrame(() => {
			el.style.transition = `transform ${durationMs}ms ease`;
			el.style.transform = '';
			el.addEventListener('transitionend', () => { el.style.transition = ''; }, { once: true });
		});
	});
}
