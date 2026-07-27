// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Lane packing for the timeline's thread capsules.
//
// NON-NEGOTIABLE RULE: a capsule is never moved off its true time to make it
// fit. Clock accuracy is the whole point of the view — if no lane is free the
// thread goes to the overflow strip instead, which is a normal, expected state
// on a busy day (peak concurrency routinely exceeds the lane count).

export const PRIORITY_RANK: Record<string, number> = {
	CRITICAL: 0,
	HIGH: 1,
	MEDIUM: 2,
	LOW: 3
};

export interface LaneInput<T> {
	key: string;
	startMin: number;
	endMin: number;
	priority: string;
	data: T;
}

export interface PlacedItem<T> extends LaneInput<T> {
	lane: number;
	top: number;
	height: number;
}

export interface PackOptions {
	lanes: number;
	/** px between vertically-adjacent capsules in the same lane. */
	gap?: number;
	/** Below this a capsule's title, latest chip and footer collide. */
	minHeight?: number;
	/** The piecewise scale's minutes → px mapping. */
	y: (minute: number) => number;
	/** Start of the linear region — capsules can't begin above it. */
	clampStart?: number;
}

export interface PackResult<T> {
	placed: PlacedItem<T>[];
	overflow: LaneInput<T>[];
}

/**
 * Pack threads into lanes, urgency first then chronology, so loud threads
 * always win a lane and quiet ones are the ones that spill.
 */
export function packLanes<T>(items: LaneInput<T>[], opts: PackOptions): PackResult<T> {
	const gap = opts.gap ?? 4;
	const minHeight = opts.minHeight ?? 98;
	const clampStart = opts.clampStart ?? -Infinity;
	const laneCount = Math.max(1, opts.lanes);

	const sorted = items
		.map((item) => ({
			...item,
			startMin: Math.max(item.startMin, clampStart),
			endMin: Math.max(item.endMin, Math.max(item.startMin, clampStart))
		}))
		.sort(
			(a, b) =>
				(PRIORITY_RANK[a.priority] ?? 2) - (PRIORITY_RANK[b.priority] ?? 2) ||
				a.startMin - b.startMin ||
				a.key.localeCompare(b.key)
		);

	const laneEnd = new Array<number>(laneCount).fill(-Infinity);
	const placed: PlacedItem<T>[] = [];
	const overflow: LaneInput<T>[] = [];

	for (const item of sorted) {
		const top = opts.y(item.startMin);
		const height = Math.max(opts.y(item.endMin) - top, minHeight);
		const lane = laneEnd.findIndex((end) => end <= top - gap);
		if (lane === -1) {
			overflow.push(item);
			continue;
		}
		laneEnd[lane] = top + height;
		placed.push({ ...item, lane, top, height });
	}

	return { placed, overflow };
}

/** Geometry of one lane column, given the lanes area's pixel width. */
export function laneGeometry(columnWidth: number, lanes: number, stripWidth: number) {
	const usable = Math.max(0, columnWidth - stripWidth);
	const laneWidth = usable / Math.max(1, lanes);
	return {
		laneWidth,
		left: (lane: number) => lane * laneWidth + 4,
		width: Math.max(60, laneWidth - 9)
	};
}

/**
 * How many lanes fit a given width. Narrow windows step 9 → 6 → 4 rather than
 * squeezing capsules below a readable width (responsive rules in the handoff).
 */
export function lanesForWidth(columnWidth: number, preferred: number): number {
	if (columnWidth < 620) return Math.min(preferred, 4);
	if (columnWidth < 1200) return Math.min(preferred, 6);
	return preferred;
}
