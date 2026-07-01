// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { parseBackendDate } from '$lib/utils/datetime';

type Pt = { x: number; y: number };

/**
 * Monotone cubic Hermite spline (Fritsch-Carlson).
 * Produces smooth SVG curves that pass through every data point
 * without introducing false peaks or valleys.
 */
export function monotonePath(points: Pt[]): string {
	const n = points.length;
	if (n < 2) return '';
	if (n === 2)
		return `M${r(points[0].x)},${r(points[0].y)}L${r(points[1].x)},${r(points[1].y)}`;

	const dx: number[] = [];
	const dy: number[] = [];
	const slopes: number[] = [];
	for (let i = 0; i < n - 1; i++) {
		dx.push(points[i + 1].x - points[i].x);
		dy.push(points[i + 1].y - points[i].y);
		slopes.push(dx[i] !== 0 ? dy[i] / dx[i] : 0);
	}

	const m: number[] = [slopes[0]];
	for (let k = 1; k < n - 1; k++) {
		if (slopes[k - 1] * slopes[k] <= 0) {
			m.push(0);
		} else {
			m.push((slopes[k - 1] + slopes[k]) / 2);
		}
	}
	m.push(slopes[n - 2]);

	for (let k = 0; k < n - 1; k++) {
		const s = slopes[k];
		if (Math.abs(s) < 1e-6) {
			m[k] = 0;
			m[k + 1] = 0;
		} else {
			const a = m[k] / s;
			const b = m[k + 1] / s;
			const h = a * a + b * b;
			if (h > 9) {
				const t = 3 / Math.sqrt(h);
				m[k] = t * a * s;
				m[k + 1] = t * b * s;
			}
		}
	}

	let d = `M${r(points[0].x)},${r(points[0].y)}`;
	for (let k = 0; k < n - 1; k++) {
		const seg = dx[k] / 3;
		d += ` C${r(points[k].x + seg)},${r(points[k].y + m[k] * seg)} ${r(points[k + 1].x - seg)},${r(points[k + 1].y - m[k + 1] * seg)} ${r(points[k + 1].x)},${r(points[k + 1].y)}`;
	}
	return d;
}

/**
 * Closed area: monotone curve closed to a horizontal baseline.
 */
export function monotoneAreaPath(points: Pt[], baseline: number): string {
	if (points.length < 2) return '';
	const line = monotonePath(points);
	const last = points[points.length - 1];
	const first = points[0];
	return `${line} L${r(last.x)},${r(baseline)} L${r(first.x)},${r(baseline)} Z`;
}

/**
 * "Nice number" step for clean axis tick values.
 */
export function niceStep(maxVal: number, targetTicks: number): number {
	if (maxVal <= 0) return 1;
	const rough = maxVal / targetTicks;
	const pow = Math.pow(10, Math.floor(Math.log10(rough)));
	const frac = rough / pow;
	let nice: number;
	if (frac <= 1.5) nice = 1;
	else if (frac <= 3) nice = 2;
	else if (frac <= 7) nice = 5;
	else nice = 10;
	return nice * pow;
}

/**
 * Format an ISO UTC timestamp as a local-time label.
 * Adapts format to the chart's window size.
 */
export function formatBucketLabel(iso: string, windowMinutes: number): string {
	const d = parseBackendDate(iso);
	if (!d) return '';
	if (windowMinutes <= 360) {
		return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
	}
	if (windowMinutes <= 1440) {
		const day = d.toLocaleDateString([], { weekday: 'short' });
		const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
		return `${day} ${time}`;
	}
	return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function r(v: number): string {
	return v.toFixed(1);
}
