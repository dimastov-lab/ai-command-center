# AI Command Center — Founder Design Principles

Status: **permanent design philosophy.** Unlike `DESIGN_SYSTEM.md` (which specifies tokens and
components for the current Streamlit application and will be revised as that implementation
evolves), this document is not implementation-bound and is not expected to change when the
product migrates from Streamlit to a native client (`IMPLEMENTATION_ROADMAP.md` UX-7). It exists
to give every future design or implementation decision — in this Streamlit application, in the
PySide6/Qt initiative (`docs/desktop/*`), or in whatever comes after both — a fixed reference
point to be judged against. When a future decision seems to conflict with something in
`DESIGN_SYSTEM.md`, `KANBAN_REDESIGN.md`, or `INTERACTION_MODEL.md`, this document is the one to
resolve the conflict in favor of.

Each principle states the rule, why it exists, and one concrete example already present in this
documentation set so it isn't just an abstraction.

## 1. Desktop-first

The product is designed for a 27–32" monitor operated by one power user at a desk, not for a
phone, a tablet, or a casual browser tab. Narrow-viewport behavior (`DESIGN_SYSTEM.md` §2.4's
"narrow fallback") is a *fallback*, not the design center — it exists so the product degrades
gracefully, not so it can be optimized for mobile use. No future increment should trade desktop
density for mobile-style spacing "just in case."

**Why**: this is a control plane for running and supervising AI agent work against real
repositories — a task that requires sustained attention at a desk, multiple simultaneous signals
on screen, and precise keyboard/mouse control, none of which a small screen supports well.

**Example**: `KANBAN_REDESIGN.md` §1's column widths are tuned for 1728–1920px+, and the board is
allowed to require horizontal scroll at 1440px rather than compressing itself to fit — width is
spent on comfort at the target size, not conserved for a size the product isn't built for.

## 2. Information-first

Every pixel earns its place by conveying a fact the user needs to act on. Decoration that carries
no information — gradients, illustrative icons, marketing-style hero sections — has no place in
this product. If a visual element can be removed without the user losing information, remove it.

**Why**: the user's job when looking at this product is almost always "what needs my attention
right now, and what do I do about it" — anything on screen that isn't answering one of those two
questions is friction, not polish.

**Example**: `DESIGN_SYSTEM.md` §3.5 defines elevation as border + background contrast, not drop
shadows, specifically because a shadow communicates nothing a border doesn't already say more
cheaply — this is Principle 2 applied to one concrete token decision.

## 3. Operational, not decorative

This is a tool for operating a system, not a showcase for a design team. Every screen should read
as instrumentation — closer to a cockpit or a monitoring console than to a marketing dashboard or
a consumer app. Visual flourishes that exist to look impressive rather than to convey state are
rejected on sight, regardless of how polished they look in isolation.

**Why**: named directly in the founding brief — the product needs to feel like Linear, Raycast,
GitHub Desktop, and a modern IDE: tools built by and for people doing real work, not tools built
to be admired.

**Example**: `VISUAL_LANGUAGE.md` §3 names the specific aesthetic failure modes ("SaaS marketing
dashboard," "gamified productivity app") this principle rules out.

## 4. Dense but readable

Favor showing more real information in less space over showing less information more spaciously
— but never past the point where a fact stops being legible at a glance. Density has a floor: no
text below 12px, no click target below 32px, no more than 3 badges in one row before the rest
moves to progressive disclosure (`DESIGN_SYSTEM.md` §4). Density is a means to the end of
"more true things visible at once," never an end in itself — a screen that's dense but illegible
has failed this principle just as much as one that's spacious but empty.

**Why**: the founder brief is explicit that the product should be "dense, calm, professional, and
operational" — dense is not in tension with calm here; a well-hierarchized dense layout reads as
*more* calm than a sparse one, because the user doesn't have to hunt across scattered space to
assemble the picture.

**Example**: `KANBAN_REDESIGN.md` §2.4's rule that a `Ready`-status badge is suppressed on the
compact card (because it's the resting state and showing it everywhere is noise) is density in
service of readability — removing a fact that isn't informative, to make room for the ones that
are.

## 5. Minimal clicks

The number of interactions between "I want to know X" or "I want to do Y" and the answer/result
should be as small as it can be without hiding information the user needs to decide safely.
Every additional click, dialog, or page navigation between intent and result is a cost that must
be justified — usually by safety (§7) or by genuinely needing more screen space than the current
context has.

**Why**: this is a daily-use operator tool. A cost that's negligible once is a real cost multiplied
across hundreds of daily interactions.

**Example**: `INTERACTION_MODEL.md` §8's Recommendation Card "Запустить" button deliberately
skips the general launcher's multi-step confirmation, because a recommendation is already
pre-validated as safe to launch — the extra friction that protects a fully general launch action
would be pure cost with no corresponding safety benefit here.

## 6. Progressive disclosure

Show the minimum that lets the user decide what to look at next; put everything else one
deliberate action away, never hidden entirely and never all shown at once. A screen's first
layer answers "what is this and does it need me"; the next layer (opened on demand) answers
"why, and what can I do about it."

**Why**: this is the structural fix for the single biggest problem this redesign exists to solve
— `UX_AUDIT.md`'s findings are almost all instances of *everything* being shown at once in not
enough space, which is what breaks both density (§4) and readability at the same time.

**Example**: the entire compact-card/Inspector split in `KANBAN_REDESIGN.md` §2 and
`DESIGN_SYSTEM.md` §9.13 is this principle given a name and a component — the compact card is
layer one, the Inspector is layer two, and nothing skips straight to layer two without the user
asking for it.

## 7. Consistency over novelty

A new screen should look like it belongs to this product before it looks interesting. Reuse an
existing token, component, or interaction pattern unless there is a specific, stated reason the
existing one doesn't fit — "it would look nicer" is not a stated reason. Two components that do
the same job should look and behave the same way everywhere they appear.

**Why**: `UX_AUDIT.md` §2.9 documented the cost of the opposite approach directly — five
independently-styled panels stacked on one page read as five different products, not one. Every
new component this redesign introduces (Inspector, Confirmation Dialog, Empty State, Error
Banner) is deliberately built once and reused everywhere the same need appears, rather than once
per page.

**Example**: `DESIGN_SYSTEM.md` §9.12 explicitly requires the Recommendation Card and Queue Item
to share the Task Card's border/radius/typography grammar rather than each inventing its own —
consistency was treated as a requirement to write down, not an assumed side effect of good taste.

## 8. Engineering-grade UX

This product is used by people who read stack traces, care about exact state, and are suspicious
of UI that hides or paraphrases the truth. Never smooth over an error into vague reassurance,
never round a number when precision is available and relevant, never claim something succeeded
when it's still in progress. Copy and status should be as precise as the system underneath it.

**Why**: the audience is engineers operating engineering systems (agent runs, git state, test
verdicts) — the UI's job is to be a faithful, low-latency window onto real system state, not a
friendly abstraction layer over it.

**Example**: `DESIGN_SYSTEM.md` §7's Error state rule — state what failed and what to do next,
raw exception detail available behind "Подробнее" rather than either hidden entirely or dumped
directly into the primary message — is precision (the truth is always available) paired with
usability (it's not forced on you at the wrong moment).

## 9. Native-first mindset

Even while the product is implemented in Streamlit, every design decision should be made as if a
native desktop application were the real target — because eventually one will be
(`docs/desktop/*`, and `IMPLEMENTATION_ROADMAP.md` UX-7). Prefer patterns a native app would use
(persistent panels, instant local state changes, keyboard-first navigation) over patterns that
only make sense because the current implementation reruns a script on every interaction. Where
Streamlit cannot deliver a native-feeling interaction reliably, say so explicitly
(`DESIGN_SYSTEM.md` §12.3) and defer it, rather than building a degraded imitation that becomes
its own legacy debt.

**Why**: this keeps the Streamlit implementation and the eventual native client converging on
the same product vision instead of diverging into two different tools that happen to share a
name — and it avoids investing engineering effort into Streamlit-specific workarounds for
interactions that were never going to be Streamlit's strength.

**Example**: `INTERACTION_MODEL.md` §7's drag-and-drop decision is this principle exactly — rather
than building a fragile custom Streamlit component to fake native drag-and-drop, the explicit
`st.selectbox` control is kept as the honest Streamlit-native answer, and true drag-and-drop is
named as a PySide6/Qt candidate feature instead.

## 10. No dashboard aesthetics

Explicitly reject the visual grammar of generic analytics/BI dashboards: large decorative KPI
tiles with oversized numerals and little else, gratuitous multi-color charts where a number or a
short list would do, gauge/speedometer widgets, and grid-of-cards layouts optimized for a
TV-mounted status screen rather than a desk-bound operator making decisions. This product surfaces
state to act on, not metrics to glance at from across a room.

**Why**: named directly in the founder brief's complaint that Project Intelligence,
Recommendations, Kanban, and Execution Queue "do not feel like one coherent desktop product" —
much of that feeling comes from the KPI strip already leaning toward dashboard-tile aesthetics
(`UX_AUDIT.md` §2.9). This principle is the explicit guardrail against drifting further that way
while making those tiles more visually polished.

**Example**: `DESIGN_SYSTEM.md` §9.5's KPI Card spec keeps the tile informational (label, value,
one hover-revealed reason) rather than decorative — no sparkline, no icon illustration, no color
gradient fill; `VISUAL_LANGUAGE.md` §3 names "dashboard aesthetics" as one of the product's
explicit non-goals.

## How to use this document

- When writing a new component spec or reviewing a proposed visual change, check it against these
  ten principles before checking it against `DESIGN_SYSTEM.md`'s tokens — the tokens implement
  these principles; they don't override them.
- If a principle and a specific token/component spec ever conflict, treat that as a bug in the
  spec, not a reason to bend the principle — file it as a correction to `DESIGN_SYSTEM.md`,
  `KANBAN_REDESIGN.md`, or `INTERACTION_MODEL.md` rather than working around it silently.
- This document does not get a version bump per implementation increment (UX-1 through UX-7,
  `IMPLEMENTATION_ROADMAP.md`) — it changes only when the founder deliberately revisits the
  product's philosophy, not when its implementation does.
