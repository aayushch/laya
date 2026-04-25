// Shared visual helpers for feed cards: platform brand dots, actor avatars.

const PLATFORM_DOT_COLORS: Record<string, string> = {
	gmail: '#EA4335',
	github: '#9CA3AF',
	bitbucket: '#2684FF',
	jira: '#2684FF',
	outlook: '#0078D4',
	calendar: '#1A73E8',
	slack: '#611F69',
	laya: '#F97316'
};

export function platformDotColor(platform: string): string {
	if (!platform) return '#6B7280';
	return PLATFORM_DOT_COLORS[platform.toLowerCase()] ?? '#6B7280';
}

// Pull the platform key out of an entity_id like "gmail:msg-123" or just "gmail".
export function platformKey(entityId?: string): string {
	if (!entityId) return '';
	return entityId.split(':')[0].toLowerCase();
}

export function actorInitials(name?: string | null): string {
	if (!name) return '?';
	const parts = name.trim().split(/\s+/).filter(Boolean);
	if (parts.length === 0) return '?';
	if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Deterministic hue (0-360) from a string — used to pick a stable avatar color
// per actor without storing one. djb2-ish hash, kept simple.
function hashHue(input: string): number {
	let h = 5381;
	for (let i = 0; i < input.length; i++) {
		h = ((h << 5) + h + input.charCodeAt(i)) | 0;
	}
	return Math.abs(h) % 360;
}

// Returns an OKLCH color tuned for dark UI: muted chroma, mid lightness.
// Same name => same color across the app.
export function actorAvatarColor(name?: string | null): string {
	const hue = hashHue((name ?? 'unknown').toLowerCase());
	return `oklch(0.62 0.11 ${hue})`;
}
