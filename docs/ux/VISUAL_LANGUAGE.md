# AI Command Center — Visual Language

Status: **permanent qualitative direction.** This document defines the *feel* the product should
produce — the qualities a screen should be judged against before anyone measures a hex value or a
pixel. `DESIGN_SYSTEM.md` §3–§8 turns these qualities into concrete tokens (spacing, type, color,
elevation) for the current Streamlit implementation; this document is the reference those tokens
serve, and it doesn't change when the tokens do. It sits alongside
`FOUNDER_DESIGN_PRINCIPLES.md` — that document governs *behavior and structure* (clicks,
disclosure, density of information), this one governs *how it should feel to look at*.

Deliberately: **no color palette lives in this document.** `DESIGN_SYSTEM.md` §3.6 already owns
the exact semantic color values, and colors are the easiest thing to get technically right while
missing the feeling entirely. A product can use exactly the right shade of indigo and still feel
like a toy, or exactly the right shade of gray and still feel cluttered — the qualities below are
what determine which one happens, independent of the specific values chosen.

## 1. The nine qualities

### Calm

Nothing on screen should compete for attention that doesn't need it. Motion is rare and always
meaningful (`DESIGN_SYSTEM.md` §3.5's animation tokens — 100–150ms, purely cosmetic, never
gating). Color is used to mean something, never to decorate — a screen with five different hues
active at once is not calm, regardless of how "friendly" each individual hue is. Calm is not the
same as empty (§Dense, below) — a dense screen can be calm if its hierarchy is clear; a sparse
screen can still feel anxious if the few things on it are loud.

*Test*: if you had to describe the screen with your eyes half-closed, would you be able to say
"one thing is wrong, here" — or would everything look like it's shouting at once?

### Dense

More true, current facts visible per square inch than a typical consumer app, without crossing
into illegible (`FOUNDER_DESIGN_PRINCIPLES.md` §4's floor: 12px minimum text, 32px minimum
targets). Density here means *information* density, not *decoration* density — a screen packed
with icons and gradients isn't dense in the sense this document means; a screen where every
element present is a distinct, load-bearing fact is.

*Test*: could you remove any single element on screen without losing information the user needs?
If yes, the screen isn't dense, it's cluttered — cut it. If no, it's dense in the right sense.

### Professional

Looks like it was built by people who understand the domain, not by people decorating a template.
No stock-illustration empty states, no cutesy copy ("Oops! Looks like there's nothing here 🎉"),
no mascot, no unnecessary emoji in structural UI (`DESIGN_SYSTEM.md` §6 — emoji are being phased
out of status communication specifically for this reason). Professional does not mean cold or
humorless in copy — it means the visual and verbal register match the seriousness of what the
tool actually does (launching agents against real repositories, tracking real work).

*Test*: would this screen look out of place if the person using it were doing something
consequential — reviewing a production deploy, debugging a failed run at 11pm? If a screen would
feel jarring in that moment, it isn't professional enough yet.

### Fast

Feels fast even when it structurally can't be instant. This is partly real (no unnecessary
network/computation between a click and a visible response) and partly perceptual: an immediate
optimistic state change, a skeleton that matches the final shape (`DESIGN_SYSTEM.md` §7's
loading-state rule), a progress bar with a real percentage rather than an indeterminate spinner
whenever the underlying operation actually reports progress. Nothing in the interface should ever
leave the user wondering whether their click registered.

*Test*: after any click, is there visible feedback within roughly 100ms — even if the real result
takes seconds? If the only feedback is the eventual result itself, it isn't fast enough.

### Precise

Numbers, labels, and states say exactly what is true, not an approximation or a friendly
paraphrase (`FOUNDER_DESIGN_PRINCIPLES.md` §8, Engineering-grade UX). A progress bar shows the
actual computed percentage, not a rounded-for-comfort one. A badge says `Failed`, not
`Something went wrong`. Precision extends to visual alignment too — text that's almost but not
quite aligned to a grid reads as sloppy in a way that undermines trust in the data next to it,
even though the misalignment has nothing to do with data correctness.

*Test*: could a fact shown on screen be more specific without becoming noisier? If yes and it
isn't, that's imprecision to fix.

### Technical

Comfortable using developer-native conventions rather than avoiding them for approachability:
monospace for anything copy-verbatim (`DESIGN_SYSTEM.md` §3.2's `type.mono` — run ids, branch
names, commit hashes, paths), real keyboard shortcuts with visible shortcut hints, git/branch/PR
terminology used directly rather than paraphrased into consumer-friendly language. The product
does not need to explain itself to a non-technical audience, because it doesn't have one.

*Test*: does any copy or affordance exist specifically to soften technical reality for a
non-technical reader? If so, it's fighting the audience this product actually has.

### Minimal

Every visual element earns its place (`FOUNDER_DESIGN_PRINCIPLES.md` §2, Information-first).
Minimal is not the same as sparse — a minimal *dense* screen is the target combination, not a
contradiction. What's cut is ornamentation: borders that don't separate anything meaningful,
icons that repeat what the adjacent label already says, containers nested inside containers with
no distinct purpose at each level.

*Test*: does every border, container, and icon on screen mark a real boundary or convey a real
fact? Anything that's there purely because "it looked bare without it" fails this test.

### High information density

Distinct from "Dense" above in emphasis: this quality is specifically about *simultaneity* — how
many independent, current facts the user can absorb in one glance without navigating anywhere.
The Kanban board showing lane, priority, execution status, and blocked-reason for every visible
card at once (`KANBAN_REDESIGN.md` §2.3's information hierarchy) is the product's clearest
expression of this quality — a well-designed dense screen replaces several navigations with one
glance.

*Test*: how many separate pages or clicks would a *less* information-dense version of this screen
require to convey the same facts? The gap is the value this quality is protecting.

### Low visual noise

The inverse measure of Calm, stated as a constraint: minimize the number of distinct visual
"voices" active on one screen at once — hue count, weight variation, border styles, icon
families. `DESIGN_SYSTEM.md` §3.6 and §6 encode this directly: one accent hue, a fixed six-color
semantic set, one icon family (Material Symbols only). Noise isn't about how much is on screen
(that's density) — it's about how many *different kinds* of visual signal compete for meaning at
once. A dense screen using one consistent visual grammar is quiet; a sparse screen using five
inconsistent ones is noisy.

*Test*: count the distinct visual "languages" active on screen (font weights, hue families, icon
styles, border treatments). If the count is higher than the number of genuinely distinct meanings
being communicated, there's noise to remove.

## 2. How the qualities relate

These nine are not independent knobs to individually maximize — several are in active tension,
and the product's visual identity is specifically the resolved position between them:

- **Dense** and **Calm** pull against each other by default; this product resolves the tension
  through hierarchy and progressive disclosure (`FOUNDER_DESIGN_PRINCIPLES.md` §6), not by
  picking one and sacrificing the other.
- **Minimal** and **High information density** look contradictory until "minimal" is understood
  as *minimal ornamentation*, not *minimal information* — the product is minimal in decoration
  and maximal in fact-per-glance, simultaneously, on purpose.
- **Fast** and **Precise** can conflict when true precision requires a real computation the UI
  would otherwise wait on — resolved by showing an honest interim state (a skeleton, a genuine
  "computing…") rather than a fabricated fast-but-wrong number.

## 3. What the product should NOT become

Naming the failure modes explicitly, because "be professional" is vague until you know which
specific things are being ruled out:

- **A SaaS marketing dashboard.** No hero KPI tiles with oversized numerals and sparkline
  flourishes, no "growth" framing on metrics that are operational facts, no onboarding-tour
  aesthetic. `FOUNDER_DESIGN_PRINCIPLES.md` §10 names this as a structural rule; this is its
  visual expression.
- **A gamified productivity app.** No streaks, no celebratory confetti/animation on task
  completion, no badges-as-rewards framing (as distinct from `st.badge`-as-status-indicator,
  which this document is not talking about), no progress bars styled to feel like a game's XP
  meter rather than a computation's actual percentage.
- **A generic admin-panel template.** No visual signals that this is "yet another CRUD app built
  on a free admin theme" — generic card-grid-of-everything layouts, default-framework color
  schemes left unmodified (the exact problem `UX_AUDIT.md` §2.5 documents today), stock
  Bootstrap/Material-off-the-shelf aesthetics with no point of view.
- **A consumer chat/social app.** No rounded bubble-style containers for non-chat content, no
  avatar-heavy social framing for what is fundamentally a single-operator tool, no infinite-scroll
  feed patterns applied to what should be a scannable, bounded board.
- **An enterprise BI tool.** No gauge/speedometer widgets, no forced multi-color pie/donut charts
  where a number or short list communicates the same fact faster, no drill-down-through-six-menus
  navigation for information that should be one glance away (directly opposed to
  `FOUNDER_DESIGN_PRINCIPLES.md` §5, Minimal clicks).
- **A design-system showcase.** No component exists to demonstrate visual range — every pattern
  in `DESIGN_SYSTEM.md` §9 is reused deliberately (`FOUNDER_DESIGN_PRINCIPLES.md` §7,
  Consistency over novelty) rather than each screen getting its own "signature" treatment.

## 4. How to use this document

When reviewing a new screen, component, or visual change, name which of the nine qualities it
strengthens and which (if any) it risks weakening, before checking it against
`DESIGN_SYSTEM.md`'s specific token values. A change that's technically token-compliant but reads
as noisy, decorative, or dashboard-like has failed this document even if it passes
`DESIGN_SYSTEM.md`'s letter — treat that as a signal to revise the change, not to relax this
document.
