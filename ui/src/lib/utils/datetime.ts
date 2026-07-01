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
