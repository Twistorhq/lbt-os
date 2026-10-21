# Pattern 02: The ghost town (unverified skeleton)

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 5.9, 5.11, 10.5. Doctrine: seeing is free; speaking requires ID.

## What this surface is

The entire product for an account that has not passed ID verification (T0). Its contents are a CLOSED LIST in TW-646 Section 5.11: growth work cannot add to it and safety work cannot subtract from it without amending the spec. This pattern renders exactly that list, nothing more. The ghost town is the shop window and the funnel: it must show a living city and make verification read as the obvious next step, while containing no voice of any kind.

Design consequence, stated once: the skeleton is enforced by read-only session scopes at the API (TW-646 Section 5.11). The UI does not hide write buttons as its enforcement; it simply never renders write affordances, because for this account state they do not exist.

## The skeleton, surface by surface

1. **Public events index (lowest band only).** Listing rows: event name, date and time, venue name and neighborhood, category, band label chip (16+ shown as a plain label; nothing above the lowest band appears at all, per fail-closed). Each row carries its named source attribution and the static map preview card (pattern 00, `SocStaticMapCard`). The primary action on every row is the outbound action to the original listing at the source (TW-646 Section 10.1). No member events, no group content, no descriptions (the index stores none, TW-646 Section 10.6).
2. **Group directory, previews only.** Public groups rated 16 plus: name, category, metro and neighborhood, band label, and a member count BAND (a range such as 25 to 50, never an exact count). The host renders only as the verified-host marker: the "Checked at the door" line, never a name, photo, or reachable profile.
3. **Group landing shells.** The preview fields, the group's next public gathering in index form (date, coarse area, band), and the verification path. No posts, no member photos, no roster, no discussion.
4. **The private corner.** The account's own settings (from Identity), their own profile draft visible to no one else, and verification status with review outcomes (pattern 01).
5. **The verify call to action.** Present on every ghost surface, stating plainly what verification unlocks. It is a persistent bottom-bar action above the tab bar, thumb-reachable, and it is the only filled button in the ghost town.

## What is structurally absent (render checklist for review)

- No feed surface at all. No messaging affordance anywhere, including no disabled or greyed message buttons (a greyed button advertises a surface the doctrine says does not exist for this state).
- No like, react, comment, or RSVP controls. Join actions route to verification (pattern 01 entry rule).
- No people search, no member directory, no Door Pass surface, no check-in surfaces. No age band chips (pattern 00) either: chips belong to people surfaces, and the ghost town has none; the host renders only as the "Checked at the door" marker, and listing band labels are content ratings, not member chips.
- A blocked or banned identity gets less than this: refusal (TW-646 Section 6), never a partial skeleton.

## Visual treatment

Day scheme. The skeleton must look like the same product the verified member uses, one room of it, not a brochure for it: same tokens, same type, same map cards. The one deliberate difference is quietness: no celebration content, no standing gold anywhere (standing does not exist here), and the verify action carries the only violet fill on the screen.

## Accessibility notes

- Band labels and count bands are text, announced as text; nothing about the skeleton is conveyed by color or by the map image alone (every map card carries a text neighborhood line).
- The outbound source action is labeled with the source name ("View at Denver Public Library"), so a screen reader user hears the attribution the sighted user sees.
