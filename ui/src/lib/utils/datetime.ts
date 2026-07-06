/**
 * Backend timestamp parsing.
 *
 * The Laya engine standardizes on UTC timestamps serialized WITHOUT a zone
 * designator, e.g. "2026-07-01 06:40:04" (space-separated, no 'Z', no offset).
 * JavaScript's `new Date("2026-07-01 06:40:04")` interprets a space-separated,
 * zone-less string as LOCAL time — so rendering it directly shifts every
 * timestamp by the viewer's UTC offset (the classic "'just now' card that is
 * actually hours off" bug).
 *
 * Normalizing to full ISO-8601 with an explicit 'Z' forces UTC interpretation.
 * We also convert the space separator to 'T' because stricter engines (WebKit,
 * i.e. the macOS WKWebView that Tauri embeds) reject "YYYY-MM-DD HH:MM:SSZ" and
 * only accept "YYYY-MM-DDTHH:MM:SSZ".
 *
 * Payloads that already carry a zone (a trailing 'Z' or a numeric offset like
 * "+00:00") are passed through untouched.
 *
 * Returns an absolute (UTC-anchored) Date; call the usual local-rendering Date
 * methods (toLocaleString, getHours, ...) to display it in the user's zone.
 * Returns null for empty/nullish input.
 */
export function parseBackendDate(s: string | null | undefined): Date | null {
	if (!s) return null;
	const utc = s.endsWith('Z') || s.includes('+') ? s : s.replace(' ', 'T') + 'Z';
	return new Date(utc);
}

/**
 * Relative "time ago" label for a backend timestamp.
 *
 * The first four tiers (just now / Xm / Xh / Xd) were byte-identical across a
 * dozen components; only the >24h tail and null-handling diverged, so those are
 * options (review §5.5 — P7-7):
 *   - `weeks: true`  — collapse 7+ days into "Xw ago" (feed cards do this;
 *     the compact list rows stop at days).
 *   - `nullLabel`    — string returned for empty/nullish input (default '';
 *     e.g. processing rules show 'never').
 *
 * Format variants that add "yesterday"/months/locale tails (chat list, omni
 * header) are deliberately NOT folded in here — they render a different shape.
 */
export function timeAgo(
	dateStr: string | null | undefined,
	opts?: { weeks?: boolean; nullLabel?: string }
): string {
	const d = parseBackendDate(dateStr);
	if (!d) return opts?.nullLabel ?? '';
	const diff = Date.now() - d.getTime();
	const mins = Math.floor(diff / 60000);
	if (mins < 1) return 'just now';
	if (mins < 60) return `${mins}m ago`;
	const hours = Math.floor(mins / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	if (opts?.weeks && days >= 7) return `${Math.floor(days / 7)}w ago`;
	return `${days}d ago`;
}
