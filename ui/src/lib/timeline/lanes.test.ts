// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { packLanes, laneGeometry, lanesForWidth, type LaneInput } from './lanes';

// 1px per minute keeps the arithmetic readable in assertions.
const y = (minute: number) => minute;

function item(key: string, startMin: number, endMin: number, priority = 'MEDIUM'): LaneInput<null> {
	return { key, startMin, endMin, priority, data: null };
}

describe('packLanes', () => {
	it('places non-overlapping threads in the same lane', () => {
		const { placed, overflow } = packLanes([item('a', 0, 100), item('b', 200, 300)], {
			lanes: 3,
			y,
			minHeight: 50
		});
		expect(overflow).toEqual([]);
		expect(placed.map((p) => p.lane)).toEqual([0, 0]);
	});

	it('pushes overlapping threads into separate lanes', () => {
		const { placed } = packLanes([item('a', 0, 300), item('b', 100, 400), item('c', 200, 500)], {
			lanes: 3,
			y,
			minHeight: 50
		});
		expect(new Set(placed.map((p) => p.lane)).size).toBe(3);
	});

	it('never shifts a capsule off its true time — extras overflow instead', () => {
		const items = [item('a', 0, 300), item('b', 10, 300), item('c', 20, 300)];
		const { placed, overflow } = packLanes(items, { lanes: 2, y, minHeight: 50 });
		expect(placed).toHaveLength(2);
		expect(overflow.map((o) => o.key)).toEqual(['c']);
		for (const p of placed) {
			expect(p.top).toBe(y(p.startMin));
		}
	});

	it('gives lanes to the loudest threads first, whatever their order', () => {
		const items = [
			item('low', 0, 300, 'LOW'),
			item('crit', 10, 300, 'CRITICAL'),
			item('high', 20, 300, 'HIGH')
		];
		const { placed, overflow } = packLanes(items, { lanes: 2, y, minHeight: 50 });
		expect(placed.map((p) => p.key)).toEqual(['crit', 'high']);
		expect(overflow.map((o) => o.key)).toEqual(['low']);
	});

	it('sorts equal priorities chronologically', () => {
		const { placed } = packLanes([item('later', 200, 250, 'HIGH'), item('earlier', 0, 50, 'HIGH')], {
			lanes: 1,
			y,
			minHeight: 10
		});
		expect(placed.map((p) => p.key)).toEqual(['earlier', 'later']);
	});

	it('enforces the minimum capsule height', () => {
		const { placed } = packLanes([item('a', 0, 5)], { lanes: 3, y, minHeight: 98 });
		expect(placed[0].height).toBe(98);
	});

	it('honours the gap when deciding a lane is free', () => {
		// 'b' starts 2px after 'a' ends — closer than the 4px gap, so it needs its own lane.
		const { placed } = packLanes([item('a', 0, 100), item('b', 102, 200)], {
			lanes: 3,
			gap: 4,
			minHeight: 10,
			y
		});
		expect(placed.map((p) => p.lane)).toEqual([0, 1]);
	});

	it('clamps starts above the linear region', () => {
		const { placed } = packLanes([item('carried', 0, 600)], {
			lanes: 3,
			y,
			minHeight: 10,
			clampStart: 480
		});
		expect(placed[0].startMin).toBe(480);
		expect(placed[0].top).toBe(480);
	});

	it('returns empty results for no input', () => {
		expect(packLanes([], { lanes: 9, y })).toEqual({ placed: [], overflow: [] });
	});
});

describe('laneGeometry', () => {
	it('splits the column between lanes, reserving the overflow strip', () => {
		const geo = laneGeometry(1000, 10, 56);
		expect(geo.laneWidth).toBeCloseTo(94.4);
		expect(geo.left(0)).toBeCloseTo(4);
		expect(geo.left(2)).toBeCloseTo(192.8);
	});
});

describe('lanesForWidth', () => {
	it('keeps the preferred lane count on a wide window', () => {
		expect(lanesForWidth(1400, 9)).toBe(9);
	});
	it('steps down on narrower windows', () => {
		expect(lanesForWidth(900, 9)).toBe(6);
		expect(lanesForWidth(500, 9)).toBe(4);
	});
	it('never raises a lower preference', () => {
		expect(lanesForWidth(1400, 5)).toBe(5);
		expect(lanesForWidth(900, 5)).toBe(5);
	});
});
