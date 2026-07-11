// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';

/** When true, the feed description panel (card / group detail) widens into a
 *  focused overlay that floats over the cards instead of pushing them aside —
 *  the same "focus mode" the chat sidebar uses (see chatExpanded). Session-only
 *  (not persisted) and reset when the panel closes, so opening a detail always
 *  starts in the default inline layout. */
export const detailExpanded = writable(false);
