# Pattern 03: Feed and celebration moments

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 4 (feed), 5.9 (inherited ratings), 7.10 (recognition), 8 (attendance visibility). Catalog: twistor-social-reward-catalog.md Section 4.

## What this surface is

The warm layer of the product: posts, celebrations, reactions, comments, in chronological order, group scoped (TW-646 Section 11). There is no algorithmic ranking and no engagement economy to farm (TW-646 Section 7.5), so the feed's design job is different from every incumbent's: it is a bulletin of a real social life, not an attention market. Standing is earned in rooms; the feed is where the room applauds.

## Composition

- Single column of posts. A post is an `article` with author line (display name, age band chip at standard size, standing tier name when shown, group name; chip rules in pattern 00), body, optional media, and the reaction row.
- The composer carries the audience row directly above it (pattern 09): the current audience in words, one tap to change, defaulting to the container. A post scoped to close friends or a picked set renders a small audience line on the post itself for its viewers ("Close friends"), so nobody mistakes a narrow post for a public one.
- Reaction kinds are the spec's three (`like`, `congrats`, `celebrate`), rendered as labeled buttons with counts, never icon-only. The distinction matters: congrats is the congratulations register Justynn specified for life events and milestones; celebrate marks the room's moments.
- Comments thread one level visibly with a "view all" expansion; the comment composer is a bottom-anchored field that grows upward inside the thumb zone.
- Media renders after EXIF-stripped ingest (TW-646 Section 4 choke point); the UI never displays original-file metadata and shows a plain note when media was removed by moderation.

## Celebration moments (catalog Section 4, spec Section 7.10)

- Celebration posts are system-generated at the moment the underlying fact lands: tier promotions, night-count milestones, series streaks, hosting milestones, district completion, venue first nights, Year One. Wording patterns are the catalog's (short, factual, proud; Twistor voice).
- Celebration rendering: Fraunces headline line, the fact stated plainly, the standing gold (`--soc-standing-fill`) used for the milestone mark only. No confetti animation, no decorative motion; the congratulations come from people reacting, not from the chrome.
- Every celebration honors the member's attendance visibility settings (TW-646 Section 8) and can be turned off by the member (the shelf still records the moment privately, catalog Section 4). A celebration never names other attendees and never publishes finer than neighborhood geography.
- Teen recognition never renders on adult surfaces and adult recognition never renders for teens (catalog Section 7); the feed inherits its container's rating (below), which is the mechanism.

## Rating inheritance (TW-646 Sections 4 and 5.9)

A post carries no rating of its own: it inherits its container's (the group's rating, or the rating of the event a recap is about), enforced server side at the query layer. UI consequence: the feed never renders a locked, blurred, or teased post. Under-band content is absent, the way blocked-member content is absent. There is no client-side hiding anywhere in this pattern; if the API returned it, the rule already failed (TW-646 Section 5.9).

## States

- Empty feed (new member, no groups yet): routes to group discovery and the public index, in that order. It never fills with suggested strangers or promoted content; an honest empty state is a funnel to rooms.
- Held or removed comments render per the moderation record (TW-646 Section 4): a removed comment leaves a plain tombstone line for its author only; teen-band comments in the under-review state render the neutral under-review notice that does not name the filter (TW-646 Section 5.10).
- Offline: the feed shows the last fetched state with its timestamp; reactions queue as recorded-on-device (pattern 00).

## Accessibility notes

- Reactions announce state changes ("Congrats added, 14 people") via live region; counts are text, not badges alone.
- Celebration headlines are real headings in the reading order, so a screen reader user hears the room's news in the same order everyone else does.
- Auto-posted moments are never the only carrier of their fact: the same milestone appears in the member's standing detail (pattern 06).
