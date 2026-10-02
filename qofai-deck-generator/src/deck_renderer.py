"""Deck Renderer — turn a design prompt into a finished HTML slide deck.

This is the automatic "Claude Code" build leg. `generate_deck_prompt`
(Module 3 + wire-together) produces the per-slide design prompt; this module
feeds that *same* content to Claude via the Anthropic API and returns a single
self-contained HTML slide deck. Both legs build from the same content. This leg
carries the pinned house style in its system prompt (`SYSTEM_PROMPT` +
`HOUSE_STYLE_CSS`); the saved Claude Design prompt carries the same formatting
decisions as a readable house-style header (`prepend_house_style_brief`,
`HOUSE_STYLE_BRIEF`), so a human pasting it into Claude Design lands in the same
visual family instead of receiving content with no formatting guidance. The
renderer strips that header (`strip_house_style_brief`) before sending, so the
content Claude Code renders is byte-for-byte the bare content prompt, never new
content — the house style reaches this leg through the system prompt, not the
user message.

The house style is pinned, not free per run (reversed ~2026-07-18; PRD §2.D,
§4). The design instruction carries a fixed, reference-derived stylesheet
(`HOUSE_STYLE_CSS`) and a per-slide structure the builder fills in, rather than
letting the builder re-derive layout, palette, and typography each run. That is
what makes the geometry deterministic — the top complaint under the free design
was run-to-run whitespace and inconsistency, and only fixed CSS removes it. The
scaffold is *derived* in style from the reference decks and carries zero of their
content (no client name, number, or copy line): every value on the deck still
comes from the prompt, so the renderer stays reusable for any client (CLAUDE.md).

There is exactly ONE theme and no options (Casey, 2026-07-21). `THEME_CSS` holds
it — one palette, one type scale, one chrome, one font (Arial everywhere), and one
blue family for Gantt phases — and every deck type composes from it:
`HOUSE_STYLE_CSS = THEME_CSS + PROPOSAL_SLIDES_CSS + PRINT_CSS` and
`STATUS_HOUSE_STYLE_CSS = THEME_CSS + STATUS_SLIDES_CSS + PRINT_CSS`. What varies
by `deck_type` is which COMPONENTS a deck has, never how it looks: the proposal
scaffold is six slides, and the status scaffold (`STATUS_SYSTEM_PROMPT`, PRD §5.4)
is a variable-count deck carrying the three net-new components a check-in needs —
the dated Gantt with a TODAY marker (bars colored by phase position, state as
annotation only), the per-workstream progress trackers, and the per-workstream
slide with its two stage-selected framings.

Credentials come from the environment (`ANTHROPIC_API_KEY`), never hardcoded.
The `anthropic` package is imported lazily so the pure pipeline (loader,
adapter, assembler, guard) and its tests keep running with no dependency.
Callers may inject a `client` (any object exposing
`client.messages.stream(...)` as a context manager with `get_final_message()`)
to test or to reuse a configured client without touching the network.
"""

import logging
import re

from html_edit_layer import stamp_slide_ids
from render_guard import protected_strings
from text_gate import AUDIENCE_EXTERNAL, apply_text_gate

# Default model for the build. Opus 4.8 is the most capable tier for a
# design-heavy HTML generation; overridable per call via `model`.
DEFAULT_MODEL = "claude-opus-4-8"

# Streaming ceiling. A full deck is well under this; streaming (below) keeps the
# large value from tripping the SDK's non-streaming timeout guard.
DEFAULT_MAX_TOKENS = 64000

# Reasoning depth for the build. High suits a multi-slide layout task;
# overridable per call.
DEFAULT_EFFORT = "high"

# How long ONE render attempt may read for, and how many attempts it gets. Sized
# against this leg's own measurement rather than against the other four: four
# isolated renders timed on 2026-09-02 ran 230.6s, 243.9s and 212.8s, and a
# fourth stalled mid-stream past the SDK's own 600-second default and raised
# `httpx.ReadTimeout`. 420 seconds is roughly 1.7x the slowest clean render, so
# a healthy render is never cut off, and a stall is answered in seven minutes
# instead of the ten-plus a bare client allowed. `model_call.attempt` retries a
# stalled stream ONCE and logs that it did: a stall is the one render failure a
# second attempt reliably fixes, and one retry is the most a leg this expensive
# should spend without a human deciding to.
DEFAULT_TIMEOUT_S = 420.0
DEFAULT_ATTEMPTS = 2

LOGGER = logging.getLogger("deck.render")

# The one stop reason that means the model finished saying what it had to say.
# Anything else means the answer was CUT, and a cut deck is the failure this leg
# degrades worst on: there is no JSON to fail parsing and no exception to catch,
# so a deck missing its last slides is a valid HTML document that renders, saves
# and looks exactly like a deck that was meant to be shorter.
COMPLETE = "end_turn"

# How much of the ceiling a healthy render may spend before it is worth saying
# so. Not a bound and not a refusal: a render at 90% of its room still produced
# a whole deck, and the only thing wrong is that the next one might not. The
# 2026-09-03 entry is the precedent -- the extraction pass was found at 94% of
# its ceiling on a run that succeeded, and that measurement is what moved the
# number before a truncation did.
HEADROOM_WARNING = 0.9


class RenderTruncated(RuntimeError):
    """The render answered and the answer was cut off.

    ITS OWN ERROR RATHER THAN `model_call.ModelCallError`, and the difference is
    the remediation. That one means a leg gave up after its attempts and its
    bound, and it tells the reviewer to re-run, because a stalled stream is
    usually not stalled twice. A truncation is the opposite: the call COMPLETED,
    it simply ran out of room, and re-running it will run out of room again.
    Telling somebody to re-run would cost them seven minutes to reach the same
    place.

    RAISED RATHER THAN RETURNED, because the alternative is writing the deck. A
    truncated render is not a failed render from the outside: it is a shorter
    HTML document that parses, renders and saves. The fidelity guard downstream
    WOULD catch it, and it would report it as missing values, which sends the
    reviewer to look at the packet. That is the 2026-09-02 lesson exactly -- a
    failed model call reported to the reviewer as missing data -- and the fix is
    the same one: name the real reason before a later guard names a symptom.

    Carries the numbers rather than only the fact, so the ceiling can be sized
    against this instance rather than against a guess.
    """

    def __init__(self, stop_reason, output_tokens, max_tokens, characters):
        self.stop_reason = stop_reason
        self.output_tokens = output_tokens
        self.max_tokens = max_tokens
        self.characters = characters
        super().__init__(
            f"the render stopped with stop_reason={stop_reason!r} rather than "
            f"{COMPLETE!r}, so the deck it returned is cut off: "
            f"output_tokens={output_tokens}, max_tokens={max_tokens}, "
            f"html_chars={characters}"
        )

# ---------------------------------------------------------------------------
# The QofAI logo mark.
#
# The angular glyph only — not the "QofAI" wordmark. The reference template decks
# (templates/*.pdf) show the mark alone: large in the cover's top-left corner and
# small in each body slide's footer, immediately before the confidentiality note.
# Source: the brand mark on qofai.com, a single-path square glyph.
#
# It is embedded as a self-contained `data:` URI and painted with CSS `mask` over
# `background-color: currentColor`, so one asset tints to any slide context (white
# on the dark cover/closing slides, ink on the cream body slides) and the deck
# stays fully portable — no external image, no network request. Defined once here
# and shared by both scaffolds (proposal + status) and the post-render safety net
# (`_ensure_logo_css`), so the asset is never duplicated or hardcoded per client.
LOGO_MARK_DATA_URI = (
    "data:image/svg+xml,"
    "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20"
    "viewBox='0%200%2027.9143%2027.9143'%3E"
    "%3Cpath%20d='M0%200V27.9143L6.97527%2020.9324V6.97527H20.9324V20.9324H13.9571"
    "V13.9571L6.97527%2020.9324V27.9143H27.9143V0H0Z'/%3E%3C/svg%3E"
)

# The logo CSS block, appended verbatim to each scaffold's stylesheet and
# re-injected by `_ensure_logo_css` if the build ever drops or mangles it. A
# second `:root` merges with the scaffold's own; later rules win, so re-injecting
# an identical block at the end of the `<style>` is always safe and idempotent.
LOGO_MARK_CSS = (
    "\n/* QofAI logo mark — mask + currentColor so it tints per slide"
    " (white on dark, ink on cream); self-contained data URI, no external file. */\n"
    ':root{--logo-src:url("' + LOGO_MARK_DATA_URI + '")}\n'
    ".logo-mark{display:inline-block;flex:none;background-color:currentColor;"
    "color:var(--ink);-webkit-mask:var(--logo-src) no-repeat center/contain;"
    "mask:var(--logo-src) no-repeat center/contain}\n"
    ".slide--dark .logo-mark{color:#fff}\n"
    ".logo-mark--lg{width:30px;height:30px}\n"
    ".logo-mark--sm{width:14px;height:14px}\n"
    ".footer .brand-foot{display:inline-flex;align-items:center;gap:8px}\n"
)

# ---------------------------------------------------------------------------
# THE ONE STANDARDIZED THEME.
#
# Exactly one theme, no options (Casey, 2026-07-21: "they should all look the
# same" — dark cover slide, cream body slides, dark final slide). Every deck this
# renderer produces, of every deck type, is built from the tokens and the chrome
# in this block: one palette, one type scale, one Gantt color family, one font.
# The per-deck-type CSS below carries only the COMPONENTS a deck type actually
# has (a proposal's value map, a status deck's progress tracker) — never its own
# palette, type scale, or chrome. Both scaffolds compose as
# `THEME_CSS + <its components> + PRINT_CSS`, so a theme change lands on both
# deck types at once and neither can drift.
#
# Derived from the reference decks (templates/*.pdf), STYLE ONLY — no client's
# name, number, or copy. The renderer emits it verbatim; the builder fills the
# classes with the prompt's content.
#
# The frame is a fixed 1280x720 (16:9) canvas per slide. A fixed pixel canvas is
# the deterministic choice: geometry does not drift with the viewport, one slide
# maps to one print page, and "fill the frame" reduces to flex/grid tracks that
# stretch to the fixed 720px height instead of pooling whitespace at the bottom
# (the slide-3 / slide-6 failure under the old free design).
#
# Three of Casey's 2026-07-21 refinements are load-bearing here and are pinned
# by tests (tests/test_deck_renderer.py): Arial everywhere (one font, nothing
# loaded), no top rule and no upper-right confidentiality (the note lives only in
# the footer's lower left), and a single blue family for Gantt phases.
# ---------------------------------------------------------------------------
THEME_CSS = """\
:root{
  --ink:#10202e;           /* primary dark: dark-slide bg, dark text on cream */
  --ink-soft:#3a4a58;      /* body text on cream */
  --paper:#f4f1ea;         /* light-slide background (cream) */
  --panel:#ffffff;         /* cards on light slides */
  --line:#ded7ca;          /* hairline rules / card borders */
  --mute:#6b7784;          /* muted small-caps labels */
  --accent:#1a6199;        /* primary accent (steel blue) */
  --accent-strong:#124e7d; /* deeper accent for small labels */
  --accent-soft:#5fa8db;   /* accent on dark slides */
  --accent-tint:#e7eef5;   /* pale blue fill (comp boxes, phase bands) */
  --pos:#2f7d4f;           /* positive / money-good metrics (green) */
  --neg:#b1442e;           /* current-state / negative metric accent (rust) */
  --gold:#c9a24a;          /* label accent on dark bands */
  --val-comp:#5fa8db;      /* value-map: QofAI comp segment (light steel blue) */
  --val-ret:#2f7d4f;       /* value-map: client-retained EBITDA (forest green) */
  --val-ev:#d3e7d9;        /* value-map: enterprise value at exit (pale green) */
  --on-dark:#f2efe6;       /* text on dark slides */
  --on-dark-soft:#aeb9c1;  /* muted text on dark slides */
  /* The Gantt phase ramp — ONE COLOR FAMILY, shades of blue, darkest to
     lightest (Casey, 2026-07-21: the multi-hue Gantt "is kind of a bad design",
     one blue per phase is "probably better for the different phases"). Every
     phase on every deck type takes a step from this one ramp, so a Gantt reads
     as one family whatever the phase count and whatever the packet's content
     happens to call a phase. */
  --phase-1:#14385c;
  --phase-2:#1f5285;
  --phase-3:#2f6fb0;
  --phase-4:#4a86c4;
  --phase-5:#a9cbe8;
  /* Arial everywhere (Casey, 2026-07-21: "if you made it just everything with
     Arial it would probably be fine" — QofAI has its own fonts but loading them
     is a hassle and Arial is the compatible default). ONE family for every
     character on the deck: body copy, headlines, and the small-caps labels
     alike. Nothing is loaded, so the deck renders identically on Mac and
     Windows with no network request. `--label` keeps the label typography its
     own hook while resolving to the same Arial stack. */
  --sans:Arial,Helvetica,sans-serif;
  --label:var(--sans);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#20262c;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;align-items:center;gap:22px;padding:22px 0}

/* Each slide is exactly one fixed 16:9 frame. */
.slide{position:relative;width:1280px;height:720px;background:var(--paper);
  padding:40px 52px 34px;display:flex;flex-direction:column;overflow:hidden}
/* Cover + closing slides: a flat, solid dark fill (Antonio, 2026-07-21 — the
   earlier radial-gradient read as a "weird blue gradient"; solid colors only). */
.slide--dark{color:var(--on-dark);background:var(--ink)}

/* Structural chrome: the cover's top bar, and every slide's footer.
   The top bar exists on the COVER ONLY and carries the header logo mark
   (unchanged, Antonio 2026-08-06) plus the deck kicker. It has NO rule under it
   and no confidentiality note: Casey, 2026-07-21, on the body slides — "you can
   get rid of that line at the top and the line and then the header, just start
   it with the eyebrow on the left" — and, on the note appearing twice, "you got
   QofAI Confidential in two places, upper right, lower left. Lower left is good.
   Footer is good. You'll free up more real estate on the page too." So there is
   no top rule anywhere on any slide, and confidentiality is a footer-left string
   only. The footer keeps its hairline (Casey: "the footer is really good"). */
.topbar{display:flex;justify-content:space-between;align-items:baseline;
  font-family:var(--label);font-size:10px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--mute);padding-bottom:10px}
.slide--dark .topbar{color:var(--on-dark-soft)}
.footer{margin-top:auto;display:flex;justify-content:space-between;gap:16px;
  font-family:var(--label);font-size:9px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--mute);padding-top:12px;border-top:1px solid var(--line)}
.slide--dark .footer{color:var(--on-dark-soft);border-color:rgba(255,255,255,.14)}

/* shared section head */
.kicker{display:flex;align-items:center;gap:8px;font-family:var(--label);font-size:11px;
  letter-spacing:.24em;text-transform:uppercase;color:var(--accent);font-weight:700;
  margin:4px 0 11px}   /* small top margin: the eyebrow is the slide's first element (no top bar) */
.slide--dark .kicker{color:var(--accent-soft)}
.kicker::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
h1,h2,h3{font-weight:700;line-height:1.05;letter-spacing:-.012em}
.headline{font-size:33px;max-width:1120px;margin-bottom:10px}
.summary{font-size:14px;line-height:1.5;color:var(--ink-soft);max-width:1120px}
.summary b,.summary strong{color:var(--ink)}

/* the content region fills everything between head and footer */
.body{flex:1;display:flex;flex-direction:column;min-height:0;margin-top:14px}

/* reviewer markers — rendered verbatim, made visible */
.flag{display:inline-block;font-family:var(--label);font-size:9px;letter-spacing:.06em;
  background:#fff2c7;border:1px solid #d9b64a;color:#7a5a08;padding:1px 6px;border-radius:3px;
  font-weight:700;vertical-align:middle}

/* ---------- The cover slide (dark) ---------- */
.cover-body{flex:1;display:flex;flex-direction:column;justify-content:center;max-width:1080px}
.prepared{font-family:var(--label);font-size:12px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--on-dark-soft);margin-bottom:28px}
.cover-title{font-size:64px;line-height:1.02;margin-bottom:24px}
.cover-title .accent{color:var(--accent-soft);display:block}
.cover-title .base{color:#fff;display:block}
.cover-sub{font-size:19px;line-height:1.45;color:var(--on-dark-soft);max-width:900px;font-weight:400}

/* ---------- Gantt phase fills — the one blue family, shared by every deck ----------
   A phase takes its ramp step by POSITION (1st phase = .phase-1, 2nd = .phase-2,
   ...), never by a hue named in the content, so no deck can reintroduce the
   multi-color Gantt. Deeper decks clamp at the last step. These live in the theme
   rather than in a deck type's components because both Gantts draw from the same
   ramp. The `.bar` geometry itself is per deck type (a proposal rollout row and a
   status lane are different shapes) and sits with those components. */
.bar.phase-1{background:var(--phase-1)}
.bar.phase-2{background:var(--phase-2)}
.bar.phase-3{background:var(--phase-3)}
.bar.phase-4{background:var(--phase-4)}
.bar.phase-5{background:var(--phase-5);color:#123a5c}
"""

# The print rules, composed last onto every scaffold: one slide to a page.
PRINT_CSS = """

@media print{
  body{background:#fff;gap:0;padding:0}
  .slide{page-break-after:always;break-after:page}
}"""

# ---------------------------------------------------------------------------
# The proposal deck's components (slides 2 through 6). Palette, type scale, and
# chrome come from THEME_CSS above; this block adds only what a six-slide
# proposal has that the theme cannot know about.
# ---------------------------------------------------------------------------
PROPOSAL_SLIDES_CSS = """\

/* ---------- Slide 2 — opportunity ---------- */
.opp-grid{flex:1;display:grid;grid-template-rows:1fr auto;gap:18px;min-height:0}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:20px;min-height:0}
/* overflow:hidden is the last-resort container, the same role it plays on
   `.gantt` below. `packet_fill`/`data_source_adapter` trim each panel's bullet
   list to what the panel holds (see src/panel_fit.py), so this should never be
   reached. When it is, the surplus is cut at the panel edge and the layout guard
   reports a CLIPPED finding, which is a visible-to-review backstop. Without it
   the surplus is painted over the build band instead, which is invisible to every
   string-level guard and is exactly how the deck of 2026-08-19 shipped: 192px of
   bullet text lying across the band, unreadable, with all four guards green. */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:20px 22px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.panel--today{border-top:4px solid var(--neg)}
.panel--after{border-top:4px solid var(--pos)}
.panel-head{font-family:var(--label);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  font-weight:700;color:var(--mute);margin-bottom:16px}
.panel--today .panel-head{color:var(--neg)}
.panel--after .panel-head{color:var(--pos)}
.metrics{display:flex;gap:24px;flex-wrap:wrap}
.metric{flex:1;min-width:42%;margin-bottom:6px}
.metric .val{font-size:24px;font-weight:700;line-height:1.1}
.panel--today .metric .val{color:var(--ink)}
.panel--after .metric .val{color:var(--pos)}
.metric .lbl{display:block;font-size:11.5px;color:var(--ink-soft);font-weight:400;margin-top:4px;line-height:1.35}
.bullets{list-style:none;margin-top:auto;padding-top:12px}
.bullets li{font-size:12.5px;color:var(--ink-soft);padding-left:15px;position:relative;
  margin-bottom:7px;line-height:1.4}
.bullets li::before{content:"";position:absolute;left:0;top:6px;width:6px;height:6px;
  border-radius:50%;background:var(--accent)}
.build-band{background:var(--ink);color:var(--on-dark);border-radius:7px;padding:17px 22px}
.build-band .bt{font-family:var(--label);font-size:11px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--gold);font-weight:700;margin-bottom:12px}
.build-phases{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.build-phases .pn{font-family:var(--label);font-size:10.5px;letter-spacing:.06em;
  color:var(--accent-soft);font-weight:700;margin-bottom:5px}
.build-phases .pd{font-size:12.5px;color:#c7d0d6;line-height:1.45}

/* ---------- Slide 3 — numbered components ---------- */
.cards{flex:1;display:grid;gap:20px;align-items:stretch;min-height:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:7px;
  border-top:4px solid var(--accent);padding:24px 22px;display:flex;flex-direction:column;min-height:0}
.card .num{font-size:38px;font-weight:800;color:var(--accent);line-height:1;margin-bottom:14px}
.card .kick{font-family:var(--label);font-size:10px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent-strong);font-weight:700;margin-bottom:10px}
.card h3{font-size:18px;margin-bottom:11px;line-height:1.16}
.card p{font-size:12.5px;color:var(--ink-soft);line-height:1.5}
.card .spacer{margin-top:auto}

/* ---------- Slide 4 — timeline (deterministic Gantt) ---------- */
/* overflow:hidden is the last-resort container: a pathological label is cut at
   the panel edge rather than spilling onto the cream slide. The layout guard
   (src/layout_guard.py) reports anything that reaches this point, so the clip is
   a visible-to-review backstop, not a silent truncation. */
.gantt{flex:1;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);
  border-radius:7px;padding:16px 20px;min-height:0;overflow:hidden}
/* The week tracks are minmax(0,1fr), not 1fr, and a trailing gutter track follows
   them — both required by the bars' min-width:max-content below (Antonio,
   2026-07-24), and the same pair the status Gantt has carried since 2026-07-23:
   minmax(0,1fr) stops a bar's max-content minimum from inflating a track's
   auto-minimum and blowing the grid past the panel, and the gutter gives a bar
   that ends on the LAST week room to grow rightward inside the panel instead of
   off its edge. The head and the rows must declare the SAME tracks or the columns
   stop lining up. Bars and week labels are still placed only on columns 2..W+1,
   so the placement rule in the render spec is unchanged. */
.gantt-head{display:grid;
  grid-template-columns:250px repeat(var(--weeks),minmax(0,1fr)) minmax(64px,112px);
  align-items:end;gap:2px;
  margin-bottom:6px;padding-bottom:6px;border-bottom:2px solid var(--ink)}
.gantt-head .corner{grid-column:1;grid-row:1;font-family:var(--label);font-size:10px;letter-spacing:.1em;
  color:var(--mute);font-weight:700}
/* `grid-row:1` is the same pin the STATUS Gantt's header has always carried on
   its `.g-corner` and every `.g-col`, and the proposal header was the one left
   without it (Antonio, 2026-09-03). A `.wk` cell is placed by column only, so
   two cells sharing a column send the second one to an implicit SECOND row: the
   header then wraps under itself and paints over the cells before it, which is
   what `decks/WTG/claude code/output-2.html` slide 4 shipped — cells three and
   four on a `top` 13px below cells one and two, cell three starting 62px left of
   where cell two ended. `_axis_columns` now hands over a monotonic axis so the
   collision cannot arise in the data either; this pin is the structural half, so
   a header can never wrap regardless of what a builder emits. The head is a
   single row by definition — it is a ruler — and `align-items:end` on the
   container keeps every cell on one baseline. */
.gantt-head .wk{grid-row:1;font-family:var(--label);font-size:10px;letter-spacing:.04em;color:var(--mute);
  font-weight:700;text-align:center}
.gantt-rows{flex:1;display:flex;flex-direction:column;min-height:0}
.phase-band{font-family:var(--label);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;
  font-weight:700;color:var(--accent-strong);background:var(--accent-tint);border-radius:4px;
  padding:5px 10px;margin:7px 0 3px}
.trow{flex:1;display:grid;
  grid-template-columns:250px repeat(var(--weeks),minmax(0,1fr)) minmax(64px,112px);
  align-items:center;
  gap:2px;border-bottom:1px solid var(--line);min-height:0}
.trow:last-child{border-bottom:none}
.trow .rlabel{grid-column:1;font-size:11px;color:var(--ink);line-height:1.2;padding-right:10px}
/* Bar height is a share of its row, so a bar fills most of its lane instead of
   floating as a thin ribbon (Antonio, 2026-07-24 — the same rule the status
   Gantt follows). The proposal rollout is a dense chart (one row per workstream,
   often nine or more), so the share is a small inset with a low max: a sparse
   four-row rollout gets taller bars, a dense one stays legible.
   min-width:max-content replaces the old overflow:hidden (Antonio, 2026-07-24).
   A short span with a longer detail used to truncate its label mid-word with no
   ellipsis and no signal — a one-week bar on a 16-week plan is ~53px wide, about
   six characters of room, and the deck shipped reading "Switch pro". A bar whose
   week span is too narrow for its detail now keeps its true start column and
   GROWS rightward to contain the text, so the words always sit on the bar. This
   is the fix the status Gantt got on 2026-07-23; the proposal one had been left
   behind. It requires the minmax(0,1fr) tracks and the trailing gutter above. */
.bar{height:calc(100% - 8px);min-height:20px;max-height:38px;
  border-radius:3px;display:flex;align-items:center;padding:0 8px;
  font-size:9.5px;color:#fff;white-space:nowrap;min-width:max-content}
/* Bar FILL comes from the theme's one blue ramp (`.phase-N`, keyed to the phase's
   position in the plan), so a rollout with two phases and one with five both read
   as a single blue family. The old two-tone `--bar-p1` / `--bar-p2` pair is gone:
   it was its own mini-palette, and it had nothing to give a third phase. */
.bar-missing{grid-column:2 / -1;font-family:var(--label);font-size:10px;color:#7a5a08}
.milestones{display:flex;gap:32px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.ms{display:flex;align-items:center;gap:10px}
.ms .dot{width:34px;height:34px;border-radius:50%;background:var(--ink);color:#fff;
  display:flex;align-items:center;justify-content:center;font-family:var(--label);font-size:12px;font-weight:700}
.ms .mtxt strong{display:block;font-size:12px}
.ms .mtxt span{font-family:var(--label);font-size:10px;letter-spacing:.06em;color:var(--mute)}

/* ---------- Slide 5 — commercial terms, an adaptive deal sheet ---------- */
/* A FLEX COLUMN, not a fixed grid, because every block but TERMS is optional
   (2026-09-23, build-plan-commercial-slide.md). The old grid declared one row
   per region, so a region that did not render put the stretch on the wrong
   child; a column of blocks has no count to keep in step. `.deal-top` holds
   INVESTMENT and RETURN side by side, and a lone one takes the full width. */
.comm{flex:1;display:flex;flex-direction:column;gap:20px;min-height:0}
.deal-top{display:grid;grid-template-columns:1fr 1.7fr;gap:20px}
.deal-top > :only-child{grid-column:1 / -1}
.invest,.returns{background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:20px 26px}
.blk-t{font-family:var(--label);font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent-strong);font-weight:700;margin-bottom:12px}
.blk-opp{font-family:var(--label);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--mute);font-weight:700;margin:8px 0 4px}
.inv-row{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  padding:12px 0;border-bottom:1px solid var(--line)}
.inv-row:last-child{border-bottom:0}
.inv-l{font-family:var(--label);font-size:12px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--ink-soft);font-weight:700}
.inv-v{font-size:28px;font-weight:800;color:var(--ink);text-align:right}
.ret{width:100%;border-collapse:collapse;font-size:15.5px}
.ret th{font-family:var(--label);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mute);font-weight:700;text-align:left;padding:0 12px 9px 0;
  border-bottom:1px solid var(--line)}
.ret td{padding:13px 12px 13px 0;border-bottom:1px solid var(--line);color:var(--ink);
  font-weight:600}
.ret tr:last-child td{border-bottom:0}
.ret .ret-case{font-family:var(--label);font-weight:700;color:var(--accent-strong)}
.ret .ret-opp td{font-family:var(--label);font-size:9.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--mute);font-weight:700;padding-top:10px}
/* TERMS takes the height the blocks above leave, so the footnote sits at the
   foot of the slide rather than under a half-empty page (seen on the first live
   render, 2026-09-23). Its boxes keep their own height. */
.deal-terms{display:grid;gap:20px;flex:1;align-content:start}
.term{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:18px 24px}
.term-l{font-family:var(--label);font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent-strong);font-weight:700;margin-bottom:8px}
.term-v{font-size:15px;color:var(--ink);line-height:1.45}
/* Empty state: one open box holding the TERMS marker, never an invented term. */
.term--empty{border-style:dashed;text-align:center}
/* Value map — one horizontal stacked bar per scenario, mapping the EBITDA gain
   to QofAI comp + client-retained EBITDA + enterprise value at exit. Replaces
   the old scenario table (which overflowed the fixed frame). Segment widths are
   set inline by the builder, proportional to a shared max so bars are
   comparable row to row; the dollar figures are shown verbatim. */
.valuemap{border:1px solid var(--line);border-radius:7px;background:var(--panel);
  padding:10px 20px 9px;display:flex;flex-direction:column;min-height:0}
.vm-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:2px}
.vm-title{font-family:var(--label);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mute);font-weight:700}
.vm-legend{display:flex;gap:15px}
.vm-key{display:flex;align-items:center;gap:6px;font-family:var(--label);font-size:8.5px;
  letter-spacing:.05em;text-transform:uppercase;color:var(--ink-soft);font-weight:700}
.vm-key i{width:11px;height:11px;border-radius:2px;display:inline-block}
.vm-key .k-comp{background:var(--val-comp)}
.vm-key .k-ret{background:var(--val-ret)}
.vm-key .k-ev{background:var(--val-ev);border:1px solid #b9d3c2}
.vm-rows{flex:1;display:flex;flex-direction:column;justify-content:space-evenly;min-height:0}
.vm-row{display:grid;grid-template-columns:186px 1fr;align-items:center;gap:16px}
.vm-label .vm-scenario{font-family:var(--label);font-size:12.5px;font-weight:700;color:var(--ink);
  letter-spacing:.02em}
.vm-row.base .vm-label .vm-scenario{color:var(--accent)}
.vm-label .vm-gain{font-size:10.5px;color:var(--ink-soft);margin-top:1px;line-height:1.3}
.vm-track{position:relative;padding-top:11px}
.vm-comp-tag{position:absolute;top:0;left:0;font-family:var(--label);font-size:9px;
  font-weight:700;color:var(--accent-strong);letter-spacing:.02em;white-space:nowrap}
.vm-bar{display:flex;height:18px;width:100%}
.vm-seg{height:100%;display:flex;align-items:center;font-size:10px;font-weight:700;
  white-space:nowrap;overflow:hidden}
.vm-seg.comp{background:var(--val-comp);border-radius:3px 0 0 3px}
.vm-seg.ret{background:var(--val-ret);color:#fff;padding-left:9px}
.vm-seg.ev{background:var(--val-ev);color:#1f5636;justify-content:flex-end;padding-right:9px;
  border-radius:0 3px 3px 0}
.footnote{font-size:9.5px;color:var(--mute);line-height:1.4;font-style:italic}

/* ---------- Slide 6 — next steps (dark) ---------- */
.steps{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:20px;min-height:0}
.step{border-top:1px solid rgba(255,255,255,.16);padding-top:14px;display:flex;flex-direction:column}
.step .stop{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:10px}
.step .num{font-size:32px;font-weight:800;color:var(--accent-soft);line-height:1}
.step .tag{font-family:var(--label);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--on-dark-soft);text-align:right;line-height:1.5}
.step h3{font-size:18px;color:#fff;margin-bottom:8px}
.step p{font-size:12.5px;color:var(--on-dark-soft);line-height:1.5}"""

# The pinned proposal stylesheet: the one standardized theme, the proposal's own
# components, then the print rules.
HOUSE_STYLE_CSS = THEME_CSS + PROPOSAL_SLIDES_CSS + PRINT_CSS

# ---------------------------------------------------------------------------
# The house-style brief — the same design decisions the CSS above (and the
# per-slide structure in SYSTEM_PROMPT below) encode, stated as prose guidance
# for the Claude Design leg.
#
# Why it exists: the Claude Code leg gets the full pinned house style via the
# system prompt. The Claude Design leg is a human pasting the saved prompt into
# Claude Design, and it used to receive content only — so its decks drifted from
# the Claude Code decks on every formatting decision. This brief carries those
# decisions into the saved Design prompt so both legs land in the same visual
# family, while leaving Claude Design free to make its own execution choices past
# the stated decisions.
#
# KEEP IN SYNC with HOUSE_STYLE_CSS and SYSTEM_PROMPT: this is the prose form of
# the same decisions. Change a palette role, a chart type, or a per-slide layout
# in one place and change it here too.
#
# When prepended to a saved Design prompt the brief is wrapped in the two
# sentinel lines below; the renderer strips everything through the end sentinel
# before calling the API, so the Claude Code leg's user message is byte-identical
# to the bare content prompt.
# ---------------------------------------------------------------------------
HOUSE_STYLE_HEADER_START = (
    "===== QOFAI DECK HOUSE STYLE — DESIGN DIRECTION (do not delete) ====="
)
HOUSE_STYLE_HEADER_END = "===== END HOUSE STYLE — DECK CONTENT FOLLOWS ====="

HOUSE_STYLE_BRIEF = """\
Build this as a QofAI proposal deck in the pinned QofAI house style below. These
formatting decisions are fixed and match the automated Claude Code build, so the
two decks read as the same family. Follow them. Past these decisions the
execution is yours: exact spacing, proportions, and decorative detail are your
call. The font is not (see TYPE). Do not change the content, do not drop the
reviewer markers, and do not swap a slide's chart type for a different one.

FORMAT
- Six slides, one self-contained deck. Each slide is a fixed 16:9 frame (treat it
  as 1280x720, one slide to a page).
- Fill the frame. Content stretches to fill each slide top to bottom; no
  whitespace pools at the bottom. Fewer items means the existing items grow, not
  blank filler rows or columns.
- No em dashes or en dashes anywhere in the copy. Use a spaced hyphen or a comma
  instead; an em dash reads as obviously AI-generated.

THEME
- ONE standardized theme, no options and no variants. Every QofAI deck of every
  type looks the same: a dark cover slide, cream body slides, a dark final slide.
  Do not offer or invent an alternate palette, a light cover, or a per-deck accent.

PALETTE (use these roles, not just these colors)
- Dark ink (#10202e): the background of the cover and closing slides, and dark
  text on the cream slides.
- Cream paper (#f4f1ea): the background of the body slides, with white (#ffffff)
  cards on top and hairline borders (#ded7ca).
- Steel blue (#1a6199): the primary accent (kickers, card top-borders, rules).
- Green (#2f7d4f): money and positive metrics only (the "after" state, retained
  EBITDA).
- Rust (#b1442e): current-state pain only (the "today" state).
- Gold (#c9a24a): small labels on the dark bands.
- Gantt bars are ONE COLOR FAMILY, shades of blue only, one shade per phase taken
  in order from darkest to lightest (#14385c, #1f5285, #2f6fb0, #4a86c4,
  #a9cbe8). A phase's position picks its shade. Never color a bar by anything
  else and never add a second hue to the chart. Value-map segments are light
  steel blue, forest green, and pale green, in that order.

TYPE
- Arial everywhere, for every character on the deck: body copy, headlines, and
  the small-caps labels (kickers, the cover's top bar, footers, tags) alike. One
  family, no second face, and nothing loaded from anywhere.
- Large, tight headlines. Kickers are uppercase, letter-spaced, small, in the
  accent color, led by a small dot.

CHROME (every slide)
- The QofAI logo mark (the angular brand glyph, not a text wordmark) appears once
  per slide, exactly as this deck's reference template places it: large in the
  cover's top-left corner, and small in every other slide's footer, immediately
  before the confidentiality note. It tints to the slide (white on the dark
  cover/closing slides, ink on the cream body slides). Do not use a text wordmark.
- A top bar on the COVER ONLY: the large logo mark on the left and a
  right-aligned kicker, with NO rule under it and no confidentiality note in it.
  Body slides have NO top bar, NO top line, NO rule across the top, and NO
  top-right confidentiality; they start directly with the eyebrow (kicker) on the
  left. Dropping all of that frees vertical space, so the rest of the slide runs
  larger.
- A footer: on the body slides, the small logo mark then confidentiality on the
  left; then QOFAI + client + deck type + date + slide N of total in the middle,
  the project name on the right. The cover's footer-left carries no mark.
  Confidentiality appears only here (bottom-left), never in a top bar.
- Reviewer markers ([MISSING: ...] and (unconfirmed, see gaps)) are shown
  verbatim and highlighted so a reviewer catches them. Never hide, drop, or
  substitute a value for them.

PER-SLIDE LAYOUT AND CHART TYPE (do not substitute the chart type)
- Slide 1, Cover: a dark slide. A large two-line title, the first line in the
  accent color and the rest in white, a "prepared for" line above it and a
  subtitle below.
- Slide 2, The Opportunity: two panels side by side, TODAY (rust top-border) vs
  AFTER (green top-border). Each panel carries one or two large metric callouts,
  as many as the data names, and a short bullet list that must FIT inside its
  panel rather than running past it. Below the two panels, a dark "the build"
  band with the build phases as columns. This is a two-column comparison, not a
  chart.
- Slide 3, The Platform: the components as equal-width numbered cards in one row
  (steel-blue top border, a big number, a kicker where the component has one, a
  title, a description), one card per component, cards stretched to fill the row.
- Slide 4, Phased Rollout: a Gantt chart. A week-column grid across the top, then
  phase bands, then one horizontal bar per workstream placed on the week columns
  it spans and colored by phase, with a milestone band of numbered dots
  underneath. The workstream name is the row label to the left of the bars; the
  workstream detail rides inside the colored bar, and the bar fills most of its
  row's height rather than reading as a thin ribbon. When a bar's week span is too
  narrow for its detail, the colored bar grows to fit so the words stay on the
  bar. Never truncate, abbreviate, or drop a detail to make it fit. Bars sit on
  the grid, not free-floating. A missing week span shows as a flagged marker,
  never a made-up bar.
- Slide 5, Commercial Terms: an adaptive deal sheet, and the least fixed slide
  in the deck. Up top, an Investment panel (the build's cost totals) beside a
  Return table (one row per case), then the Terms as a row of labelled boxes,
  then a stacked-bar value chart only when its figures are given, then an
  italic footnote. Draw only the blocks the content provides; an absent block
  leaves no gap, heading or placeholder.
- Slide 6, Next Steps: a dark slide. The action items as a two-column grid of
  numbered steps, each with a number, a week-and-owner tag, a title, and a
  description."""


def prepend_house_style_brief(content_prompt, deck_type="proposal", *, preferences=""):
    """Return the Claude Design deliverable: the house-style header + content.

    Wraps the ``deck_type``'s house-style brief in the two (shared) sentinel
    lines and prepends it to the bare content prompt, so the saved
    ``generated-prompt-N.txt`` carries the formatting decisions a human needs when
    pasting into Claude Design. ``strip_house_style_brief`` is the exact inverse
    and is deck-type-agnostic (the sentinels are the same for both paths).

    ``preferences`` is an optional standing-reviewer-preferences block
    (``preference_store.render_preferences_block``). When non-empty it is placed
    inside the header, after the brief, so the Claude Design leg carries the same
    format-only refinements the Claude Code leg gets through its system prompt —
    the two legs stay matched. An empty string (the default, and the
    no-preferences case) yields byte-identical output to the header alone, and
    because the block sits inside the sentinels ``strip_house_style_brief`` still
    removes it, keeping the rendered content prompt clean.
    """
    brief = HOUSE_STYLE_BRIEFS.get(deck_type, HOUSE_STYLE_BRIEF)
    prefs_section = f"\n\n{preferences}" if preferences else ""
    return (
        f"{HOUSE_STYLE_HEADER_START}\n"
        f"{brief}{prefs_section}\n"
        f"{HOUSE_STYLE_HEADER_END}\n\n"
        f"{content_prompt}"
    )


def strip_house_style_brief(prompt):
    """Drop a leading house-style header if present; else return unchanged.

    The inverse of ``prepend_house_style_brief``. A prompt that does not start
    with the header sentinel (a bare content prompt, a directly-assembled prompt,
    an old saved prompt) is returned untouched, so the renderer and the guards
    are robust whether or not the header is there.
    """
    if not prompt.startswith(HOUSE_STYLE_HEADER_START):
        return prompt
    end = f"{HOUSE_STYLE_HEADER_END}\n"
    idx = prompt.find(end)
    if idx == -1:
        return prompt
    return prompt[idx + len(end):].lstrip("\n")


# The system instruction. It governs output *format only* — it must not add,
# drop, or invent slide content. The prompt (user message) is the sole source
# of what goes on the deck. The house style is fixed; the builder fills it.
SYSTEM_PROMPT = (
    "You are assembling a QofAI proposal deck into a single HTML document. The "
    "visual design is FIXED: a pinned house style is given below as a stylesheet "
    "and a per-slide structure. Do not invent, vary, or 'improve' the layout, "
    "palette, or typography. Your only job is to place the prompt's content into "
    "that fixed structure, faithfully and completely.\n\n"
    "INPUT — a per-slide design prompt: a deck-wide preamble of recurring fields, "
    "then one `## Slide N — Title` section per slide, each with `key: value` "
    "lines (lists as `- item`; records as numbered `field: value` blocks).\n\n"
    "OUTPUT — ONE complete HTML document and nothing else. Start with "
    "`<!doctype html>`. No markdown fences, no commentary before or after.\n\n"
    "CONTENT RULES (these outrank layout — never sacrifice a field to fit):\n"
    "- Render every field the prompt carries, on its slide, in the prompt's "
    "order. Do not invent, omit, summarize, or reword content.\n"
    "- Preserve reviewer markers verbatim and visibly: `[MISSING: ...]` and "
    "`(unconfirmed, see gaps)`. Keep their exact characters; wrap each in "
    "`<span class=\"flag\">` so a reviewer sees the gap. This includes recurring "
    "fields used in the footer — render a `[MISSING: ...]` value as-is, never "
    "substitute a fallback for it.\n"
    "- When a key carries `... [render under heading \"LABEL\"]`, show the value "
    "under that visible heading.\n"
    "- Self-contained: emit the stylesheet below verbatim inside one `<style>` "
    "block in the `<head>`. No external fonts, scripts, images, or network "
    "requests.\n\n"
    "FILL THE FRAME — no empty space (a top requirement):\n"
    "- Each `<section class=\"slide\">` is one fixed 16:9 frame. Its content "
    "fills the frame; no whitespace pools at the bottom.\n"
    "- The scaffold's content grids already stretch to the frame height. Keep "
    "them stretched: set the inline template values the per-slide notes ask for "
    "(column counts, `--weeks`, bar spans) so N items fill N tracks. Never add "
    "empty rows, empty columns, or filler elements, and never pad a short list "
    "with blank tracks — fewer items just means the existing tracks grow.\n\n"
    "THE PINNED STYLESHEET — emit exactly as given, once, in the `<head>`:\n"
    "<style>\n" + HOUSE_STYLE_CSS + LOGO_MARK_CSS + "\n</style>\n\n"
    "PER-SLIDE STRUCTURE (use these classes; repeat item elements to match the "
    "data count).\n\n"
    "CHROME — the COVER carries a `.topbar`; EVERY slide carries a `.footer`. "
    "NON-COVER slides have NO top bar at all: they begin directly with their "
    "`.kicker` (the eyebrow). Do not emit a `.topbar`, a top rule, or a top-right "
    "confidentiality note on any non-cover slide — confidentiality appears ONLY in "
    "the footer (bottom-left). This frees vertical space for the slide's content.\n"
    "  THE QOFAI LOGO MARK is the brand glyph, painted by CSS from an EMPTY span "
    "(`<span class=\"logo-mark ...\" aria-label=\"QofAI\"></span>`) — never put text "
    "inside it, and never use a text wordmark anywhere. It appears once per slide, "
    "exactly where the pinned template places it.\n"
    "  TOPBAR (COVER ONLY): a left `<span class=\"logo-mark logo-mark--lg\" "
    "aria-label=\"QofAI\"></span>` (the large mark, unchanged) then a right span = "
    "`deck_kicker`. Nothing else goes in it: no confidentiality note, and no rule "
    "under it. No other slide emits a `.topbar`.\n"
    "  FOOTER: the small mark lives there on the non-cover slides — see the "
    "FOOTER section below.\n\n"
    "Slide 1 — Cover: `<section class=\"slide slide--dark\">` with the cover topbar "
    "above. Then `<div class=\"cover-body\">` with `<div class=\"prepared\">` "
    "= `prepared_for`, an `<h1 class=\"cover-title\">` rendering `project_title` on "
    "two lines — first line in `<span class=\"accent\">`, the remainder in "
    "`<span class=\"base\">` (same words, split at a natural break) — and "
    "`<p class=\"cover-sub\">` = `subtitle`.\n\n"
    "Slide 2 — The Opportunity: `.kicker` = `section_label`, `.headline` "
    "= `opportunity_headline`, `.summary` = `opportunity_summary`. Then "
    "`<div class=\"body\"><div class=\"opp-grid\">`: a `.cols2` holding two `.panel` "
    "cards — `.panel--today` (head `TODAY`) and `.panel--after` (head = "
    "`after_horizon`). In each: a `.metrics` row carrying ONE `.metric` block per "
    "metric field the data actually names — a panel whose data names one metric "
    "gets one block and never an empty second one, which is the general rule above "
    "applied here. Each field is `VALUE · LABEL`: render VALUE in `.val` and LABEL "
    "in `.lbl`. A field carrying no ` · ` has no label, so emit its `.val` alone "
    "rather than an empty `.lbl`. Then a "
    "`.bullets` list (`today_pain_bullets` / `after_capability_bullets`). Below "
    "cols2, a `.build-band`: `.bt` = the first line of `build_summary`, then "
    "`.build-phases` with one `.ph` per remaining phase line (`.pn` label + `.pd` "
    "description). Omit the build-band only if `build_summary` is absent.\n\n"
    "Slide 3 — The Platform: `.kicker` (where the slide carries "
    "`platform_section_label`, the kicker is that value exactly), `.headline` = "
    "`platform_headline`, "
    "`.summary` = `platform_summary`. Then `<div class=\"body\"><div class=\"cards\" "
    "style=\"grid-template-columns:repeat(N,1fr)\">` where N = number of "
    "`components`. One `.card` per component: `.num` = `number`, `.kick` = "
    "`kicker`, `<h3>` = `title`, `<p>` = `description`. Emit the `.kick` line "
    "only for a component whose data names a `kicker`; a component with no "
    "`kicker` line renders the card without one, and never an empty `.kick` or a "
    "substitute for it.\n\n"
    "Slide 4 — Phased Rollout (timeline): where the slide carries "
    "`plan_section_label`, `.kicker` is that value exactly, which names which "
    "opportunity's schedule this is on a deck carrying several. Otherwise "
    "`.kicker` is the literal word TIMELINE "
    "and nothing else. It is fixed on this slide (Antonio, 2026-09-20) because "
    "the slide is the schedule on every deck, and a kicker written per deck "
    "gave it a different name each time — PROJECT PLAN on one, PHASED ROLLOUT "
    "on another — for a reader who is looking at the same thing. Do not extend "
    "it with the programme's length, the phase count or the client. "
    "`.headline` = `plan_headline`, "
    "`.summary` = `plan_summary`. Then `<div class=\"body\"><div class=\"gantt\">`.\n"
    "  WHAT `.summary` IS FOR, because this slide has three places that could "
    "carry the same words and must not. `plan_summary` is the one line that "
    "speaks about the PROGRAMME — what it does end to end, how many phases it "
    "runs in, where its milestones fall. The Gantt directly beneath it already "
    "draws every phase, its span and its detail, and slide 2's build strip above "
    "it already lists each phase's own note. So render `plan_summary` exactly as "
    "given and NOTHING ELSE in that element: never grow it with phase labels, "
    "phase notes, workstream detail, milestone text, or anything else off this "
    "slide, and never let it become a concatenation of the per-phase lines. If "
    "`plan_summary` is one sentence, `.summary` is one sentence.\n"
    "  Deterministic geometry, and the axis is in the PLAN'S OWN UNIT — the data "
    "states it and you never convert it. `timeline_columns` names its unit in its "
    "first entry (`WEEKS 1-6`, `MONTHS 0-3`, `Wk 1-2`); every later entry is a "
    "bare range in that same unit. Read the highest number across the entries as "
    "the axis total W. Put `style=\"--weeks:W\"` on `.gantt-head` and on every "
    "`.trow` (the CSS custom property is named `--weeks` for historical reasons "
    "and means the axis column count, in whatever unit the plan states). One "
    "shared placement rule — an element covering A through B gets "
    "`style=\"grid-column:{A+1} / {B+2}\"`. Where consecutive entries are stated "
    "as boundaries (one ending on the number the next starts on), place them so "
    "they do not overlap: the shared number belongs to the earlier one's end.\n"
    "  THE HEADER IS EXACTLY ONE ROW, and this is a hard constraint rather than "
    "a preference: put `style=\"grid-row:1\"` on the `.corner` and on EVERY "
    "`.wk` (alongside that cell's `grid-column`), and never place two `.wk` "
    "cells on a column they share. A grid pushes the second occupant of a "
    "column onto an implicit SECOND row, so one shared column wraps the rest of "
    "the header underneath itself and paints it over the cells before it. "
    "`timeline_columns` arrives monotonic — each entry starts where the previous "
    "one ended — so placing each cell by its own range already lays them end to "
    "end on one row. If two entries would still meet on a column, that column "
    "belongs to the EARLIER cell's end and the later cell starts after it: "
    "shorten the later cell, never the earlier one, and never drop a cell to "
    "avoid the collision. The `.trow` bars are NOT under this constraint — each "
    "sits on its own row, phases may genuinely run concurrently, and a bar keeps "
    "its true span even where it overlaps the one above it.\n"
    "    · `.gantt-head` starts with a `<div class=\"corner\">` naming that unit "
    "in the singular, uppercased, followed by ` →` (`WEEK →` for a plan stated in "
    "weeks, `MONTH →` for one stated in months). If no entry names a unit, the "
    "corner carries `→` alone — never a unit the data did not state. Then one "
    "`.wk` per `timeline_columns` entry, placed by that entry's own range and "
    "on `grid-row:1`, carrying that entry's text exactly as given.\n"
    "    · For each phase group, emit a `.phase-band` (the row's `phase` text), "
    "then one `.trow` per workstream in it. Each `.trow`: a `.rlabel` = "
    "`workstream` (the workstream NAME only, outside the bars), then one `.bar` "
    "placed by the workstream's `weeks` span carrying `workstream_detail` as its "
    "text inside the bar. A row that carries NO `workstream` line at all is the "
    "phase itself rather than a workstream inside it: emit it as a single "
    "`.trow` whose `.rlabel` is its own `phase` text and whose one `.bar` is "
    "placed by that row's span and carries NO TEXT AT ALL. The bar is the span; "
    "the `.rlabel` beside it is the name. Printing the phase text in both puts "
    "the same words on the slide twice in the same row and, because a bar grows "
    "rightward to contain its own text, pushes the bar past the axis column it "
    "is supposed to sit under, so nothing in the chart lines up any more. Do not "
    "invent a workstream for that row, do not repeat its label, and do not "
    "substitute other text: an empty bar spanning its own months is the whole "
    "point of the row. The name lives in "
    "`.rlabel` (left of the bars); the "
    "detail lives inside the colored bar — ALWAYS inside it, never floating "
    "beside it, and never shortened to fit. A bar whose week span is too narrow "
    "for its detail keeps its true start column and grows rightward to contain "
    "the text (the stylesheet handles this via `min-width`), so the words stay on "
    "the bar. BAR COLOR is one blue family and nothing else: the Nth phase group "
    "in the plan's order gives its rows `class=\"bar phase-N\"` — the first phase "
    "group `bar phase-1`, the second `bar phase-2`, and so on, clamping at "
    "`phase-5` if a plan has more than five phases. Never mix in another hue, "
    "never color a bar by anything but its phase's position, and never carry a "
    "color name out of the content.\n"
    "    · If a workstream's `weeks` is a `[MISSING: ...]` marker, render the "
    "marker (in a `.bar-missing` element, wrapped in `<span class=\"flag\">`) and "
    "do NOT fabricate a bar span.\n"
    "  End with a `.milestones` band: one `.ms` per `milestones` record — a "
    "`.dot` = `id`, then `.mtxt` with `<strong>` = `label` and `<span>` = "
    "`week`.\n\n"
    "Slide 5 — Commercial Terms, an adaptive deal sheet: `.kicker`, `.headline` "
    "= `terms_headline`, `.summary` = `terms_summary`. Then `<div class=\"body\">"
    "<div class=\"comm\">` holding these blocks, in this order, and ONLY the "
    "ones whose data the prompt carries. A block with no data is not drawn at "
    "all: no container, no heading, no placeholder, no marker.\n"
    "    · `.deal-top`, drawn when `investment_rows` or `return_rows` is present, "
    "holding `.invest` then `.returns` (either alone is fine).\n"
    "      `.invest`: a `.blk-t` reading \"Investment\", then one `.inv-row` per "
    "`investment_rows` record with `.inv-l` = `label` and `.inv-v` = `value`. "
    "When a record carries `opportunity`, put a `.blk-opp` naming it above that "
    "opportunity's rows.\n"
    "      `.returns`: a `.blk-t` reading \"Return\", then `<table class=\"ret\">` "
    "with a header row \"Case\" plus one column per field that ANY record "
    "carries, in this order: `annual_ebitda` (\"Annual EBITDA\"), `margin` "
    "(\"Margin\"), `payback` (\"Payback\"). One row per `return_rows` record, "
    "its first cell `.ret-case` = `scenario`. When records carry `opportunity`, "
    "put a `<tr class=\"ret-opp\">` row naming it above that opportunity's "
    "cases. Never add a total row and never sum across opportunities.\n"
    "    · `.deal-terms`, ALWAYS drawn, with `style=\"grid-template-columns:"
    "repeat(N,1fr)\"` (N = the number of `terms_rows` records, at most 3, or 1 "
    "when there are none): one `.term` per record, `.term-l` = `label` and "
    "`.term-v` = `value`, exactly as given. If `terms_rows` is the marker "
    "`[MISSING: terms_rows]`, draw ONE `.term.term--empty` with `.term-l` "
    "reading \"Terms\" and `.term-v` holding `<span class=\"flag\">[MISSING: "
    "terms_rows]</span>`. Never invent a term.\n"
    "    · `.valuemap`, ONLY when `value_mapping` is present: a horizontal "
    "stacked-bar chart (NOT a table). A `.vm-head` holding a `.vm-title` "
    "(\"Mapping EBITDA gain to value\") and a `.vm-legend` of three `.vm-key` "
    "swatches: `<i class=\"k-comp\">` QofAI comp, `<i class=\"k-ret\">` Client "
    "retained EBITDA, `<i class=\"k-ev\">` Enterprise value at exit. Then "
    "`.vm-rows` with one `.vm-row` per record: a `.vm-label` (`.vm-scenario` = "
    "`scenario`, `.vm-gain` = `ebitda_gain`) and a `.vm-track` holding a "
    "`.vm-comp-tag` (= `qofai_comp`) and a `.vm-bar` of three segments, "
    "`.vm-seg.comp` (no text), `.vm-seg.ret` (text = `client_retained_ebitda`) "
    "and `.vm-seg.ev` (text = `enterprise_value`), each with an inline width.\n"
    "    · `.footnote` = `terms_footnote`, drawn only when present.\n"
    "  Show every figure verbatim as given. Code rewrites these blocks' contents "
    "from the same data after the render, so the class names above are the "
    "contract: keep them exactly.\n\n"
    "Slide 6 — Next Steps: `<section class=\"slide slide--dark\">`. `.kicker` "
    "(where the slide carries `next_steps_section_label`, the kicker is that "
    "value exactly), "
    "`.headline` = `next_steps_headline`, `.summary` = `next_steps_summary`. Then "
    "`<div class=\"body\"><div class=\"steps\">`: one `.step` per `action_items` "
    "record — a `.stop` row with `.num` = `number` and a `.tag` = `week` · "
    "`owner`, then `<h3>` = `title` and `<p>` = `description`.\n\n"
    "FOOTER (every slide): `<div class=\"footer\">` with three spans — left, "
    "middle, right. Middle = "
    "`QOFAI + {client_short} · {deck_type_label} · {month year} · NN / TT` "
    "(built from the recurring fields; NN is this slide's position, TT the slide "
    "count), right = `project_name` (or `footer_right` on the cover). Render "
    "`client_short` exactly as given, marker and all. The LEFT span: on EVERY "
    "NON-COVER slide, wrap the small logo mark and the confidentiality note "
    "together — `<span class=\"brand-foot\"><span class=\"logo-mark logo-mark--sm\" "
    "aria-label=\"QofAI\"></span>{confidentiality}</span>`; on the COVER, the left "
    "span is just `footer_left` with no mark (the cover's mark is in the top "
    "bar).\n\n"
    "This is a draft for human review before anything ships to a client."
)


# ===========================================================================
# The status deck's components (PRD §5.4 — the three net-new renderer parts: the
# dated Gantt with a TODAY marker, the per-workstream progress trackers, and the
# per-workstream slide with its two stage-selected framings).
#
# Palette, type scale, chrome, the cover, and the Gantt's blue ramp all come from
# THEME_CSS: there is exactly ONE theme and a status deck does not get its own
# (Casey, 2026-07-21 — "they should all look the same"). The status deck used to
# carry a second palette here (a maroon accent over a warmer cream), which is
# what made a check-in deck and a proposal deck read as two products; that block
# is gone and every role it defined now resolves to the shared token.
#
# Derived from the reference check-in PDF (templates/*.pdf), STYLE ONLY — no
# client's name, number, or copy. What a status deck genuinely needs beyond the
# theme, and all this block adds: it is time-aware (dated week columns, a TODAY
# marker on the current week, "Week N of M"), and its slide count is variable (one
# workstream slide per active workstream), so the footer denominator is
# data-driven ({total_slides}).
# ===========================================================================
STATUS_SLIDES_CSS = """\

/* ---------- Project Tracking slide — dated Gantt + TODAY marker ---------- */
.gantt{flex:1;position:relative;display:flex;flex-direction:column;background:var(--panel);
  border:1px solid var(--line);border-radius:7px;padding:24px 18px 10px;min-height:0;overflow:hidden}
/* padding-top is generous so the TODAY tab can sit ABOVE the week-header row
   (Antonio, 2026-07-23) instead of over the week labels. */
/* One grid: a header row, then one row per lane; the TODAY marker spans all rows.
   Set --weeks (column count) and grid-template-rows inline (auto + one 1fr per lane). */
/* minmax(0,1fr) pins the week columns to an equal share: without it, a bar's
   min-width:max-content (below) would inflate a track's auto-minimum and blow the
   grid past the panel, clipping the last weeks. With it, a narrow bar grows over
   its (empty) neighbor cells to fit its label without widening the timeline. */
/* A trailing gutter track after the week columns gives a bar that ends on the
   LAST week room to grow rightward to fit its label WITHOUT leaving the panel and
   being clipped (Antonio, 2026-07-23: the timeline used to leak off the right
   edge). Bars are still placed only on columns 2..W+1; the gutter just absorbs a
   rightmost label's overflow inside the panel. */
.gantt-grid{flex:1;display:grid;grid-template-columns:210px repeat(var(--weeks),minmax(0,1fr)) minmax(64px,96px);
  column-gap:2px;min-height:0;position:relative}
.g-corner{grid-column:1;font-family:var(--label);font-size:9px;letter-spacing:.08em;
  color:var(--mute);font-weight:700;align-self:end;padding-bottom:7px}
.g-col{align-self:end;text-align:center;padding-bottom:7px}
.g-col .wid{font-family:var(--label);font-size:11px;font-weight:700;color:var(--ink)}
.g-col .wdate{display:block;font-family:var(--label);font-size:9px;color:var(--mute);margin-top:1px}
.g-headrule{grid-column:1 / -1;border-bottom:2px solid var(--ink);height:0;align-self:end}
.lname{grid-column:1;font-family:var(--label);font-size:9.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--ink-soft);font-weight:700;padding-right:8px;align-self:center}
.lane-rule{grid-column:1 / -1;border-bottom:1px solid var(--line);height:0;align-self:start;pointer-events:none}
/* The label always sits INSIDE the colored bar (Casey/Antonio, 2026-07-21: the
   words must be on the bar, not floating beside it). A bar whose true week span
   is too narrow for its label keeps its colored fill and GROWS to the right to
   contain the text (min-width:max-content, no clipping), so the label never
   spills onto the empty lane. */
/* Bar height is a SHARE of its lane row, not a fixed 32px (Antonio, 2026-07-24:
   the bars left a thick empty band above and below and read as thin ribbons —
   a bar should fill most of its lane). The lane is a 1fr track of a
   definite-height grid, so the percentage resolves; the min/max keep a 3-lane
   Gantt from rendering absurdly fat bars and a 9-lane one from rendering
   hairlines, so this holds for any lane count rather than one deck's. */
.bar{align-self:center;height:calc(100% - 20px);min-height:28px;max-height:60px;
  border-radius:3px;display:flex;align-items:center;padding:0 9px;
  font-size:11px;color:#fff;line-height:1.2;white-space:nowrap;min-width:max-content;z-index:1}
/* Bar FILL comes from the theme's one blue ramp (`.phase-N`, keyed to the phase
   category's position in the plan). The older hue-named fill classes are gone:
   they were a multi-color scheme wearing blue paint, and content naming a hue
   could still imply one. A phase's position is the only thing that picks a color
   now, so nothing in the content can put a second hue on a Gantt.
   State is annotation only: a buffer/upcoming bar reads as dashed/dimmed,
   never a different color.
   The buffer fill is a SOLID pale blue, not transparent (Antonio, 2026-07-24).
   Transparent left the dashed outline looking half-empty wherever the bar grew
   past its week span to fit its label, or wherever it sat over a neighboring
   bar — the dashed box read as "the bar does not fill the dotted line". An
   opaque pale fill makes the dashed box read as one filled block. */
.bar.state-buffer{background:#e8f1fa;border:1.5px dashed var(--phase-5);color:#1f5285}
.bar.state-upcoming{opacity:.55}
/* z-index:0 keeps the dashed TODAY line BEHIND the bars, so it never draws over
   a bar's label (Casey, 2026-07-21). The TODAY tab is lifted ABOVE the week-header
   row into the panel's top padding (Antonio, 2026-07-23), so it never sits over a
   week label or a bar. */
.today-mark{grid-row:1 / -1;border-left:2px dashed var(--ink);position:relative;z-index:0;pointer-events:none}
/* top:-15px seats the tab just above the dashed line's first dash (Antonio,
   2026-07-24: at -20px it floated with a visible gap and read as detached). */
.today-tab{position:absolute;top:-15px;left:50%;transform:translateX(-50%);
  font-family:var(--label);font-size:8.5px;font-weight:700;letter-spacing:.1em;
  background:var(--ink);color:#fff;padding:2px 7px;border-radius:3px;white-space:nowrap}
/* NO legend strip under the Gantt. The category color key went first (Antonio,
   2026-07-23 — the bars are labeled in their lanes, so a color key is
   redundant), and the slip/buffer callout strip went with it (Antonio,
   2026-07-24 — two bullets reading "Deployment buffer" and a slip sentence were
   uninterpretable next to the timeline that already shows both, and they were
   printed in two different colors on top of it). `slip_or_buffer_markers` is now
   annotation input only: it tells the renderer which bars carry buffer/slip
   state, exactly as `bar_categories` tells it which phase a bar belongs to (and
   so which ramp step it takes) without itself appearing on the slide. */

/* ---------- Slide 3… — Workstream Status (two stage-selected framings) ---------- */
.ws-body{flex:1;display:grid;grid-template-rows:minmax(0,1fr) minmax(0,1.02fr);gap:12px;min-height:0}
.frames{display:grid;grid-template-columns:1fr 1fr;gap:16px;min-height:0}
.frame{background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:18px 22px;display:flex;flex-direction:column;min-height:0}
/* Current state is rust and target is green, the same two roles the proposal
   deck's TODAY / AFTER panels use — one theme, so the same meaning carries the
   same color on both deck types. (This framing used to be maroon, from the status
   deck's own palette.) */
.frame--a{border-top:4px solid var(--neg)}   /* current state (TODAY / WHERE WE ARE) */
.frame--b{border-top:4px solid var(--pos)}       /* target (AFTER / TARGET) */
.frame-head{font-family:var(--label);font-size:12.5px;letter-spacing:.13em;text-transform:uppercase;
  font-weight:700;margin-bottom:16px}
.frame--a .frame-head{color:var(--neg)}
.frame--b .frame-head{color:var(--pos)}
.f-metrics{display:flex;gap:26px;flex-wrap:wrap;margin-bottom:10px}
.f-metric{flex:1;min-width:40%}
.f-metric .val{font-size:31px;font-weight:800;line-height:1}
.frame--a .f-metric .val{color:var(--ink)}
.frame--b .f-metric .val{color:var(--pos)}
.f-metric .lbl{display:block;font-size:12.5px;color:var(--ink-soft);margin-top:6px;line-height:1.4}
.f-bullets{list-style:none;margin-top:auto;padding-top:11px}
.f-bullets li{font-size:13.5px;color:var(--ink-soft);padding-left:17px;position:relative;
  margin-bottom:8px;line-height:1.45}
.f-bullets li:last-child{margin-bottom:0}
.f-bullets li::before{content:"";position:absolute;left:0;top:8px;width:6px;height:6px;
  border-radius:50%;background:var(--accent);opacity:.6}
.frame--b .f-bullets li::before{background:var(--pos)}
/* progress tracker */
.progress{background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:16px 20px;display:flex;flex-direction:column;min-height:0}
.progress-head{display:flex;justify-content:space-between;align-items:center;
  border-bottom:1px solid var(--line);padding-bottom:9px;margin-bottom:0}
.progress-label{font-family:var(--label);font-size:13.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent-strong);font-weight:700}
.progress-right{font-family:var(--label);font-size:10.5px;letter-spacing:.1em;color:var(--mute);font-weight:700}
/* one column per progress group, plus the workstream's next-steps box last.
   Set grid-template-columns inline: repeat(n_groups,1fr) then 1.15fr. */
.progress-cols{flex:1;display:grid;gap:16px;align-items:stretch;min-height:0}
/* space-evenly distributes a group's items down the (tall, frame-filling) column
   so residual height spreads as even gaps instead of pooling at the bottom. */
.pgroup{display:flex;flex-direction:column;justify-content:space-evenly;min-width:0}
.pgroup-head{display:flex;align-items:center;gap:8px;margin-bottom:0}
.pgroup-name{font-family:var(--label);font-size:11.5px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--ink-soft);font-weight:700}
.badge{font-family:var(--label);font-size:10px;letter-spacing:.05em;border:1px solid var(--mute);
  border-radius:3px;padding:2px 7px;color:var(--mute);font-weight:700;white-space:nowrap}
.pitem{display:flex;gap:9px;align-items:flex-start;margin-bottom:0}
.chk{width:17px;height:17px;border-radius:3px;flex-shrink:0;margin-top:1px;display:flex;
  align-items:center;justify-content:center;font-size:11px;line-height:1}
.chk.done{background:var(--pos);color:#fff}
.chk.pending{background:#fff;border:1.5px solid var(--mute)}
.chk.in_process{background:linear-gradient(90deg,var(--phase-3) 0 50%,#fff 50% 100%);
  border:1.5px solid var(--phase-3)}
.ptext{font-size:13.5px;color:var(--ink);line-height:1.35}
.ptext .pdetail{display:block;font-size:11.5px;color:var(--mute);margin-top:3px;line-height:1.35}
/* The per-workstream next-steps box is tinted by the workstream's POSITION
   (1st / 2nd / 3rd), in shades of the one blue family — the same house rule the
   Gantt and the closing slide follow. It used to rotate green / blue / maroon,
   which read as three different statuses when it only ever meant "the second
   workstream". A 4th or later workstream keeps the last step. */
.ws-next{border-radius:6px;padding:14px 18px;border-left:4px solid;display:flex;flex-direction:column}
.ws-next.step-1{background:#e4edf7;border-color:var(--phase-2)}
.ws-next.step-2{background:#eaf1f8;border-color:var(--phase-3)}
.ws-next.step-3{background:#f0f5fa;border-color:var(--phase-4)}
.ws-next-head{font-family:var(--label);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-soft);font-weight:700;margin-bottom:0}
.ws-next ul{list-style:none;flex:1;display:flex;flex-direction:column;justify-content:space-evenly}
.ws-next li{font-size:13.5px;color:var(--ink);padding-left:18px;position:relative;
  margin-bottom:0;line-height:1.4}
.ws-next li::before{content:"\\2192";position:absolute;left:0;color:inherit;font-weight:700}

/* ---------- Slide N — Next Steps (dark, one column per workstream) ---------- */
.nsteps{flex:1;display:grid;gap:36px;min-height:0}   /* grid-template-columns inline: repeat(n_cols,1fr) */
.nscol{display:flex;flex-direction:column;min-width:0}
.nscol-head{font-family:var(--label);font-size:13px;letter-spacing:.13em;text-transform:uppercase;
  font-weight:700;padding-bottom:10px;border-bottom:2px solid;margin-bottom:4px}
/* SHADES OF BLUE ONLY on the closing slide (Antonio, 2026-07-24), the same house
   rule the Gantt follows: green / blue / maroon columns read as three unrelated
   statuses when they are just the 1st, 2nd, and 3rd workstream. The classes are
   positional (`step-1` = the leftmost column), so light-to-dark reads as one
   family on the dark slide however many columns there are. */
.nscol.step-1 .nscol-head{color:#cfe2f4;border-color:var(--phase-5)}
.nscol.step-2 .nscol-head{color:#a1c5e6;border-color:var(--phase-4)}
.nscol.step-3 .nscol-head{color:#78a6d8;border-color:var(--phase-3)}
/* flex:1 makes each step cell divide the column equally and align-content:center
   parks its content in the middle, so N steps fill the column with even bands. */
.nstep{flex:1;display:grid;grid-template-columns:auto 1fr;gap:16px;align-content:center;
  padding:18px 0;border-top:1px solid rgba(255,255,255,.08)}
.nstep:first-of-type{border-top:none}
.nsnum{font-size:34px;font-weight:800;line-height:1}
.nscol.step-1 .nsnum{color:#cfe2f4}
.nscol.step-2 .nsnum{color:#a1c5e6}
.nscol.step-3 .nsnum{color:#78a6d8}
.nstep h3{font-size:20px;color:#fff;margin-bottom:7px}
.nstep p{font-size:13.5px;color:var(--on-dark-soft);line-height:1.55}"""

# The pinned status stylesheet: the same one standardized theme the proposal deck
# gets, the status deck's own components, then the print rules.
STATUS_HOUSE_STYLE_CSS = THEME_CSS + STATUS_SLIDES_CSS + PRINT_CSS

# The status house-style brief — the prose form of the same decisions, prepended
# to the saved Claude Design prompt on the status path. KEEP IN SYNC with
# STATUS_HOUSE_STYLE_CSS and STATUS_SYSTEM_PROMPT.
STATUS_HOUSE_STYLE_BRIEF = """\
Build this as a QofAI project status / check-in deck in the pinned QofAI house
style below. These formatting decisions are fixed and match the automated Claude
Code build, so the two decks read as the same family. Follow them. Past these
decisions the execution is yours. Do not change the content, do not drop the
reviewer markers, and do not swap a slide's chart type for a different one.

FORMAT
- A status deck has a VARIABLE slide count: cover, then project tracking, then
  ONE slide per active workstream (in the prompt's order), then next steps. Do
  not assume five. The footer's page count reads {total_slides} from the content.
- Each slide is a fixed 16:9 frame (treat it as 1280x720, one slide to a page).
  Fill the frame top to bottom; no whitespace pools at the bottom.
- No em dashes or en dashes anywhere in the copy. Use a spaced hyphen or a comma
  instead; an em dash reads as obviously AI-generated.
- Time-aware: the dated week columns, the TODAY marker's placement, and
  "Week N of M" come from the content, never from today's date.

THEME AND TYPE
- ONE standardized theme, no options and no variants, and it is the SAME theme a
  QofAI proposal deck uses: a dark cover slide, cream body slides, a dark final
  slide, steel-blue accent. A check-in deck does not get its own palette.
- Arial everywhere, for every character on the deck: body copy, headlines, and the
  small-caps labels (kickers, the cover's top bar, footers, badges, week columns)
  alike. One family, no second face, nothing loaded.

PALETTE (roles, not just colors)
- Dark ink (#10202e): cover and next-steps slide backgrounds, dark text on cream.
- Cream paper (#f4f1ea): body-slide backgrounds, white cards, hairline borders.
- Steel blue (#1a6199): the primary accent — the cover title's first line,
  kickers, the accent span of a workstream name, small labels.
- Rust (#b1442e): the current-state framing only (Frame A), the same role the
  proposal deck's TODAY panel carries.
- Green (#2f7d4f): target/after state (Frame B) and completed (done) progress
  items.
- Gantt bars are ONE COLOR FAMILY, shades of blue only (#14385c, #1f5285,
  #2f6fb0, #4a86c4, #a9cbe8), one shade per phase category taken in the order the
  categories are listed. The category's position picks the shade; the color name
  in the content picks nothing and must never reach the deck. The earlier
  multi-hue Gantt read as unprofessional. A bar's state (complete / in_progress /
  upcoming / buffer) only changes annotation (a dashed buffer, dimming), never
  the color.
- The per-workstream next-steps boxes and the closing slide's columns are also
  shades of that one blue, one per position (1st, 2nd, 3rd). Never green / blue /
  maroon: three hues read as three statuses when they only mean 1st, 2nd, 3rd.

CHROME (every slide)
- The QofAI logo mark (the angular brand glyph, not a text wordmark) appears once
  per slide, exactly as this deck's reference template places it: large in the
  cover's top-left corner, and small in every other slide's footer, immediately
  before the confidentiality note. It tints to the slide (white on the dark
  cover/closing slides, ink on the cream body slides). Do not use a text wordmark.
- Top bar on the COVER ONLY: the large logo mark left and a right-aligned kicker,
  with NO rule under it and no confidentiality note in it. Body slides have NO top
  bar, NO top line, NO rule across the top, and NO top-right confidentiality; they
  start directly with the eyebrow (kicker) on the left, which frees vertical space
  for content.
  Footer: on the body slides, the small logo mark then confidentiality on the
  left, then QOFAI + client + engagement label + month/year + slide N of
  {total_slides} in the middle, project week on the right. The cover's
  footer-left carries no mark. Confidentiality appears only in the footer
  (bottom-left), never in a top bar.
- Reviewer markers ([MISSING: ...] and (unconfirmed, see gaps)) are shown
  verbatim and highlighted. Never hide, drop, or substitute a value for them.

PER-SLIDE LAYOUT AND CHART TYPE (do not substitute the chart type)
- Cover (dark): a large two-line title, first line in the accent blue, second
  white; a "prepared for" line above and a subtitle below.
- Project Tracking: a DATED Gantt. A week-column grid across the top labeled with
  the dated columns, one horizontal bar per timeline item placed on the week
  columns it spans and colored by its phase category, grouped into lanes. A
  dashed TODAY marker drops a vertical line on the current week's column, with
  the TODAY tab seated tight above the line, not floating away from it. A buffer
  bar annotates distinctly (a dashed outline over a pale fill, filled edge to
  edge), never as a different bar color and never as a half-empty outline. Bars
  sit on the grid, not free-floating, and the colored bar fills most of its
  lane's height rather than reading as a thin sliver. The label always sits
  INSIDE the colored bar; when a bar's span is too narrow for its label, the
  colored bar grows to fit so the words stay on the bar, never floating beside
  it. Nothing goes under the grid: no category color key and no slip/buffer note
  strip. The labeled bars in their lanes and the summary line above already carry
  both, so a legend reads as unexplained decoration.
- Workstream Status (one per active workstream): two framing panels side by side
  — Frame A (current state, rust top border) and Frame B (target, green top
  border), each with large metric callouts and a short bullet list — over a
  full-width progress tracker: one column per progress group, each with a status
  badge and a done/pending/in-process checklist (green check = done, empty box =
  pending, half-blue box = in process), plus the workstream's next-steps box.
- Next Steps (dark): one column per workstream, each a numbered list (01/02/03)
  of actions with a title and description. The column heads and numbers are
  accented in SHADES OF BLUE ONLY, one blue per column position, the same house
  rule the Gantt follows. Do not accent the columns green / blue / maroon: three
  hues read as three unrelated statuses when they are only the 1st, 2nd, and 3rd
  workstream."""

# The status system instruction — governs output FORMAT only, mirroring
# SYSTEM_PROMPT. The pinned status stylesheet and a per-slide structure the
# builder fills; the prompt (user message) is the sole source of content.
STATUS_SYSTEM_PROMPT = (
    "You are assembling a QofAI project status / check-in deck into a single "
    "HTML document. The visual design is FIXED: a pinned house style is given "
    "below as a stylesheet and a per-slide structure. Do not invent, vary, or "
    "'improve' the layout, palette, or typography. Your only job is to place the "
    "prompt's content into that fixed structure, faithfully and completely.\n\n"
    "INPUT — a per-slide design prompt: a deck-wide preamble of recurring fields, "
    "then one `## Slide N — Title` section per slide, each with `key: value` "
    "lines (lists as `- item`; records as numbered `field: value` blocks). The "
    "deck has a VARIABLE number of slides: a cover, a project-tracking slide, "
    "one workstream slide per workstream section in the prompt, then a next-steps "
    "slide. Emit exactly the slides the prompt carries, in order — never assume a "
    "fixed count.\n\n"
    "OUTPUT — ONE complete HTML document and nothing else. Start with "
    "`<!doctype html>`. No markdown fences, no commentary before or after.\n\n"
    "CONTENT RULES (these outrank layout — never sacrifice a field to fit):\n"
    "- Render every field the prompt carries, on its slide, in the prompt's "
    "order. Do not invent, omit, summarize, or reword content.\n"
    "- Preserve reviewer markers verbatim and visibly: `[MISSING: ...]` and "
    "`(unconfirmed, see gaps)`. Keep their exact characters; wrap each in "
    "`<span class=\"flag\">` so a reviewer sees the gap. This includes recurring "
    "fields used in the footer.\n"
    "- Time-aware fields are load-bearing: the dated week columns, the TODAY "
    "marker's column, and the progress states (done / pending / in_process) come "
    "straight from the prompt — render them verbatim, never re-derive from a "
    "date or synthesize a state.\n"
    "- Self-contained: emit the stylesheet below verbatim inside one `<style>` "
    "block in the `<head>`. No external fonts, scripts, images, or network "
    "requests.\n\n"
    "FILL THE FRAME — no empty space (a top requirement):\n"
    "- Each `<section class=\"slide\">` is one fixed 16:9 frame. Its content "
    "fills the frame; no whitespace pools at the bottom.\n"
    "- The scaffold's content grids stretch to the frame height. Set the inline "
    "template values the per-slide notes ask for (column counts, `--weeks`, grid "
    "rows, bar spans) so N items fill N tracks. Never add empty rows/columns or "
    "filler; fewer items just means the existing tracks grow.\n\n"
    "THE PINNED STYLESHEET — emit exactly as given, once, in the `<head>`:\n"
    "<style>\n" + STATUS_HOUSE_STYLE_CSS + LOGO_MARK_CSS + "\n</style>\n\n"
    "PER-SLIDE STRUCTURE (use these classes; repeat item elements to match the "
    "data count).\n\n"
    "CHROME — the COVER carries a `.topbar`; EVERY slide carries a `.footer`. "
    "NON-COVER slides have NO top bar at all: they begin directly with their "
    "`.kicker` (the eyebrow). Do not emit a `.topbar`, a top rule, or a top-right "
    "confidentiality note on any non-cover slide — confidentiality appears ONLY in "
    "the footer (bottom-left). This frees vertical space for the slide's content.\n"
    "  THE QOFAI LOGO MARK is the brand glyph, painted by CSS from an EMPTY span "
    "(`<span class=\"logo-mark ...\" aria-label=\"QofAI\"></span>`) — never put text "
    "inside it, and never use a text wordmark anywhere. It appears once per slide, "
    "exactly where the pinned template places it.\n"
    "  TOPBAR (COVER ONLY): a left `<span class=\"logo-mark logo-mark--lg\" "
    "aria-label=\"QofAI\"></span>` (the large mark, unchanged) then a right span = "
    "`deck_kicker`. Nothing else goes in it: no confidentiality note, and no rule "
    "under it. No other slide emits a `.topbar`.\n"
    "  FOOTER: the small mark lives there on the non-cover slides — see the "
    "FOOTER section below.\n\n"
    "Slide 1 — Cover: `<section class=\"slide slide--dark\">` with the cover topbar "
    "above. Then `<div class=\"cover-body\">` with `<div class=\"prepared\">` "
    "= `prepared_for`, an `<h1 class=\"cover-title\">` with `<span class=\"accent\">` "
    "= `deck_title_accent` and `<span class=\"base\">` = `deck_title_primary`, and "
    "`<p class=\"cover-sub\">` = `status_subtitle`.\n\n"
    "Project Tracking slide: `.kicker` = `tracking_section_label`, `.headline` = "
    "`tracking_headline`, `.summary` = `tracking_summary`. Then "
    "`<div class=\"body\"><div class=\"gantt\">` containing a "
    "`<div class=\"gantt-grid\">`.\n"
    "  Deterministic geometry. Read `timeline_columns` (ordered records with "
    "`id`/`date`/`label`); let W = their count and index them 1..W in order. Put "
    "`style=\"--weeks:W; grid-template-rows:auto repeat(L,1fr)\"` on `.gantt-grid`, "
    "where L = the number of distinct `gantt_bars` lanes. Placement rule: a cell "
    "on column index C is `grid-column:{C+1}` (column 1 is the 210px lane label); "
    "a bar spanning `start_week`..`end_week` (look up their indices A..B in "
    "`timeline_columns`) is `grid-column:{A+1} / {B+2}`. Row rule: the header is "
    "grid-row 1; each lane is the next grid-row in first-appearance order.\n"
    "    · Header: a `<div class=\"g-corner\" style=\"grid-row:1\">WEEK</div>`, then "
    "one `<div class=\"g-col\" style=\"grid-column:{C+1};grid-row:1\">` per "
    "`timeline_columns` entry with `<span class=\"wid\">`=`id` and "
    "`<span class=\"wdate\">`=`label`. Add `<div class=\"g-headrule\" "
    "style=\"grid-row:1\"></div>`.\n"
    "    · For each distinct `gantt_bars` `lane` (in first-appearance order, on "
    "its own grid-row R): a `<div class=\"lname\" style=\"grid-row:R\">` = the lane "
    "text, a `<div class=\"lane-rule\" style=\"grid-row:R\"></div>`, then one "
    "`<div class=\"bar phase-N\" style=\"grid-column:{A+1} / {B+2};grid-row:R\">` "
    "per bar in that lane. BAR COLOR is one blue family and nothing else. N is the "
    "bar `category`'s POSITION in `bar_categories` (the first category listed is "
    "`phase-1`, the second `phase-2`, and so on, clamping at `phase-5` when there "
    "are more than five categories), so every bar of a category takes the same "
    "ramp step and the Gantt reads as one blue family. IGNORE the category's "
    "`color` field entirely: it is a legacy content field naming a hue, it does "
    "not select anything, and a hue name must never reach the deck. The fill is "
    "the CATEGORY'S, never the state's. ALWAYS put the bar `label` INSIDE the "
    "colored bar; never place the "
    "label beside the bar. A bar whose week span is too narrow for its label keeps "
    "its true start column and grows rightward to contain the text (the stylesheet "
    "handles this via `min-width`), so the words always sit on the bar. If the "
    "bar's `state` is `buffer` add class `state-buffer`; if `upcoming` add "
    "`state-upcoming` (annotation only — do not change the hue).\n"
    "    · The TODAY marker: let T = the index of `today_marker_week` in "
    "`timeline_columns`; emit `<div class=\"today-mark\" style=\"grid-column:{T+1};"
    "grid-row:1 / -1\"><span class=\"today-tab\">` = `today_marker_label` `</span></div>`.\n"
    "  Nothing goes under the grid. Do NOT render ANY legend, key, or callout "
    "strip below the Gantt — no category color key, no slip/buffer note list. Two "
    "reference fields feed the bars without ever appearing on the slide: "
    "`bar_categories` (it tells you what a category is and, by its position in the "
    "list, which blue its bars take) and `slip_or_buffer_markers` (it tells you which weeks "
    "carry a buffer or a slip, which is already visible as the bar's dashed "
    "`state-buffer` annotation and in `tracking_summary`). Neither `covers` text, "
    "nor a category's `color` name, "
    "nor a marker `label` may be printed anywhere on the slide. The grid is the "
    "last element inside `.gantt`.\n\n"
    "Workstream Status slide (one per workstream section): `.kicker` = "
    "`section_label`; `.headline` renders `workstream_name_full` with the "
    "`workstream_name_accent` substring wrapped in `<span style=\"color:var(--accent)\">`; "
    "`.summary` = `workstream_summary` (bold its final hook sentence if present). "
    "Then `<div class=\"body\"><div class=\"ws-body\">`:\n"
    "    · `.frames` with two panels. `.frame.frame--a`: `.frame-head` = "
    "`frame_a_label`, a `.f-metrics` of `.f-metric` blocks (each `frame_a_metrics` "
    "item is `VALUE · LABEL` — split on the FIRST ` · `; VALUE in `.val`, the rest "
    "in `.lbl`), then a `.f-bullets` list of `frame_a_bullets`. `.frame.frame--b`: "
    "same with `frame_b_label` / `frame_b_metrics` / `frame_b_bullets`.\n"
    "    · `.progress`: a `.progress-head` with `.progress-label` = `progress_label` "
    "and, if present, `.progress-right` = `progress_right_label`. Then a "
    "`.progress-cols` with `style=\"grid-template-columns:repeat(G,1fr) 1.15fr\"` "
    "where G = the number of distinct `progress_items` groups. One `.pgroup` per "
    "group (in first-appearance order): a `.pgroup-head` with `.pgroup-name` = the "
    "group `group` and a `.badge` = that group's `status_label`, then one `.pitem` "
    "per item — a `<span class=\"chk {state}\">` (done → a `\\2713` check; pending → "
    "empty; in_process → half) and a `.ptext` = the item `label` (if the label "
    "contains ` · `, put the part before it as the item and the rest in a "
    "`<span class=\"pdetail\">`). After the groups, a `.ws-next.step-{n}` box, where "
    "n is this workstream slide's POSITION (1 for the first workstream slide, 2 for "
    "the second, 3 for the third and for any beyond it): a `.ws-next-head` "
    "\"NEXT STEPS\" and a `<ul>` of `ws_next_steps`.\n\n"
    "Next Steps slide (dark): `<section class=\"slide slide--dark\">`. `.kicker` = "
    "`next_steps_section_label`, `.headline` = `next_steps_headline`, `.summary` = "
    "`next_steps_summary`. Then `<div class=\"body\"><div class=\"nsteps\" "
    "style=\"grid-template-columns:repeat(K,1fr)\">` where K = the number of "
    "distinct `next_steps_items` `column_title` values. One `.nscol.step-{n}` per "
    "column, n by column POSITION (1 for the leftmost, then 2, then 3 for the third "
    "and any beyond it) — the pinned stylesheet gives each position a shade of the "
    "one blue family, so the closing slide reads as one family like the Gantt: a "
    "`.nscol-head` = `column_title`, then one `.nstep` per item in that column — "
    "`.nsnum` = `number`, `<h3>` = `title`, `<p>` = `body`.\n\n"
    "FOOTER (every slide): `<div class=\"footer\">` with three spans — left, "
    "middle, right. Middle = "
    "`QOFAI + {client_short} · {footer_engagement} · {month_year} · NN / {total_slides}` "
    "(NN is this slide's position, the denominator is `total_slides` exactly, "
    "never a hardcoded count), right = `project_week`. Render `client_short` and "
    "`total_slides` exactly as given, marker and all. The LEFT span: on EVERY "
    "NON-COVER slide, wrap the small logo mark and the confidentiality note "
    "together — `<span class=\"brand-foot\"><span class=\"logo-mark logo-mark--sm\" "
    "aria-label=\"QofAI\"></span>{confidentiality}</span>`; on the COVER, the left "
    "span is just `confidentiality` with no mark (the cover's mark is in the top "
    "bar).\n\n"
    "This is a draft for human review before anything ships to a client."
)

# Per-deck-type render assets, selected by ``deck_type``.
SYSTEM_PROMPTS = {"proposal": SYSTEM_PROMPT, "status": STATUS_SYSTEM_PROMPT}
HOUSE_STYLE_BRIEFS = {"proposal": HOUSE_STYLE_BRIEF, "status": STATUS_HOUSE_STYLE_BRIEF}


def _strip_code_fences(text):
    """Strip a wrapping markdown code fence if the model added one.

    The system prompt forbids fences, but models occasionally wrap HTML in
    ```html ... ```. Remove a leading fence line and a trailing ``` so the saved
    file is valid HTML rather than HTML-inside-markdown. A response with no fence
    is returned unchanged (only surrounding whitespace trimmed).
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    # Drop the opening fence line (the ``` or ```html marker).
    lines = lines[1:]
    # Drop a trailing fence line if present.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# House text gate: the deterministic anti-AI-voice pass (NEXT-STEPS item 10).
#
# The em dash is an obvious "written by AI" tell, and the founder advisor asked
# for none anywhere on a deck (Casey, 2026-07-21 and again at the 2026-07-23
# sync: "I hate that, no M dashes throughout"). That rule is now one of four
# format rules plus the banned-vocabulary table that `text_gate` applies to the
# finished HTML, replacing the dash-only normalization this module used to do.
#
# Two things make it safe to run on a rendered deck. It rewrites text nodes only,
# so the pinned stylesheet and every attribute come through byte-for-byte. And it
# masks every factual span before a rule runs, extended here with
# `render_guard.protected_strings` — so no value the fidelity guard is
# responsible for is even visible to the gate. `verify=True` (the default) then
# re-derives the factual index of input and output and raises rather than
# returning a deck whose facts moved.
#
# It is the Claude Code leg's counterpart to the house-style rules the saved
# Claude Design prompt carries, so both build legs land on the same house voice.
# ---------------------------------------------------------------------------
def _apply_house_text_gate(html, content_prompt, deck_type):
    """Run the deterministic text gate over a freshly rendered deck."""
    return apply_text_gate(
        html,
        audience=AUDIENCE_EXTERNAL,  # a deck is client-facing copy
        protect=protected_strings(content_prompt, deck_type),
    ).html


# ---------------------------------------------------------------------------
# Gap-flag removal: keep the "(unconfirmed, see gaps)" marker OFF the deck.
#
# House decision (Antonio, 2026-07-21): a gap flag is an internal review note,
# not client-facing deck copy. The reviewer works a flagged-claims checklist in
# the review UI instead, and resolving a flag there never re-renders the deck.
# So the finished deck is always clean of these markers.
#
# This is applied by `generate_and_save_deck` AFTER the render-fidelity guard has
# verified the markers survived the render (the guard's hard "no dropped marker"
# rule still holds against the model's raw output), and only then, just before
# the file is written. It is deliberately NOT part of `render_deck_html`, whose
# output the guard inspects. Only the "(unconfirmed, see gaps)" flag is removed;
# a `[MISSING: ...]` marker (a genuinely absent value the reviewer still supplies)
# is left untouched.
# ---------------------------------------------------------------------------
_GAP_FLAG_SPAN_RE = re.compile(
    r"[ \t]*<span[^>]*\bclass=\"[^\"]*\bflag\b[^\"]*\"[^>]*>\s*"
    r"\(unconfirmed,\s*see gaps\)\s*</span>",
    re.IGNORECASE,
)
_GAP_FLAG_BARE_RE = re.compile(r"[ \t]*\(unconfirmed,\s*see gaps\)", re.IGNORECASE)


def strip_gap_flags(html):
    """Remove every ``(unconfirmed, see gaps)`` flag from a rendered deck.

    Drops the whole highlighted ``<span class="flag">(unconfirmed, see gaps)</span>``
    (and any bare, unwrapped occurrence), leaving the value it annotated in place
    and clean. Idempotent, and a no-op on a deck that carries no gap flags. Does
    not touch ``[MISSING: ...]`` markers.
    """
    html = _GAP_FLAG_SPAN_RE.sub("", html)
    return _GAP_FLAG_BARE_RE.sub("", html)


def _ensure_logo_css(html):
    """Guarantee the QofAI logo asset is present and correct in the output.

    The build reproduces the pinned stylesheet, but a ~240-character ``data:``
    URI is the one fragment a model is most likely to drop or subtly alter. If the
    exact logo data URI is not already in the document, inject the canonical
    ``LOGO_MARK_CSS`` at the end of the first ``<style>`` block — a later rule
    wins, so this repairs a mangled copy and is a no-op when the build emitted the
    asset verbatim. The markup hooks (the empty ``.logo-mark`` spans) come from the
    system prompt; this secures the asset those hooks paint from.

    Only repairs within an existing ``<style>`` block: a real deck always carries
    the pinned stylesheet, so a document with no style block is a degenerate case
    (not a deck) that the logo net should leave untouched rather than paper over.
    """
    if LOGO_MARK_DATA_URI in html:
        return html
    end_style = html.find("</style>")
    if end_style == -1:
        return html
    return html[:end_style] + LOGO_MARK_CSS + html[end_style:]


def _check_complete(message, html, max_tokens):
    """Refuse a cut-off render, and record what a healthy one cost.

    WHY THIS LEG NEEDS IT MOST. The extraction pass answers JSON, so a truncation
    there fails to parse and raises on its own. This one answers HTML, and a
    truncated HTML document is still a document: it parses, it renders, it saves,
    and a deck missing its last two slides looks exactly like a deck that was
    meant to have fewer. Nothing downstream distinguishes the two on sight.

    IT MATTERS MORE SINCE ITEM 15. A deck's slide count is the reviewer's now,
    not the template's, and there is no ceiling on it, so the render's answer
    grows with the number of opportunities while its ceiling stays where it was
    sized for six slides.

    AND THE HEALTHY CASE IS RECORDED TOO, which is the half that stops the next
    number being a guess. `DEFAULT_MAX_TOKENS` on this leg has never been
    measured: every figure in this module's comments is a DURATION, because the
    bound was what kept failing. A render that completes logs what it spent, and
    one that spends most of its room says so before a truncation makes the point
    the expensive way. That is the 2026-09-03 shape, where the extraction pass
    was found at 94% of its ceiling on a run that SUCCEEDED, and the measurement
    moved the number before a failure did.

    Read through `getattr` for the same reason `paper_extraction._measured` is:
    every injected client in this suite is a stand-in with a `content` list, and
    a check that crashed on the object it is checking would be worse than none.
    A response that states no stop reason at all is not judged, because a fake
    that never set one is not a model that answered badly.
    """
    stop_reason = getattr(message, "stop_reason", None)
    output_tokens = getattr(getattr(message, "usage", None), "output_tokens", None)
    if stop_reason is not None and stop_reason != COMPLETE:
        raise RenderTruncated(stop_reason, output_tokens, max_tokens, len(html))
    if output_tokens is None or not max_tokens:
        return
    spent = output_tokens / max_tokens
    LOGGER.log(
        logging.WARNING if spent >= HEADROOM_WARNING else logging.INFO,
        "render completed: output_tokens=%s of max_tokens=%s (%.0f%% of the "
        "ceiling), html_chars=%s",
        output_tokens, max_tokens, spent * 100, len(html),
    )


def render_deck_html(
    prompt,
    *,
    deck_type="proposal",
    model=DEFAULT_MODEL,
    client=None,
    api_key=None,
    max_tokens=DEFAULT_MAX_TOKENS,
    effort=DEFAULT_EFFORT,
    system_prompt=None,
    preferences="",
    timeout_s=DEFAULT_TIMEOUT_S,
    attempts=DEFAULT_ATTEMPTS,
):
    """Render one design prompt into a self-contained HTML slide deck.

    ``prompt`` is either the bare content prompt from ``generate_deck_prompt`` or
    the saved Claude Design prompt (that content plus a house-style header) — the
    same text a human would paste into Claude Design. A leading house-style header
    is stripped before sending, so this leg renders the bare content either way.
    ``client`` may be injected (anything
    exposing ``messages.stream(...)`` as a context manager yielding
    ``get_final_message()``); when omitted, a real ``anthropic.Anthropic`` client
    is constructed and reads ``ANTHROPIC_API_KEY`` from the environment (or the
    explicit ``api_key``). Returns the HTML document as a string.

    ``deck_type`` selects the pinned house style (its system prompt) when
    ``system_prompt`` is not given explicitly — ``proposal`` (the six-slide
    scaffold) or ``status`` (the variable-count scaffold with the three net-new
    components: the dated Gantt with a TODAY marker, the progress trackers, and
    the per-workstream two-framing slide).

    ``preferences`` is an optional standing-reviewer-preferences block
    (``preference_store.render_preferences_block``). When non-empty it is appended
    to the system prompt so the render applies the captured format-only
    refinements — the Claude Code side of the reviewer feedback loop, matching the
    same block the Claude Design leg carries in its house-style header. An empty
    string (the default) leaves the system prompt byte-identical to the pinned
    house style. It is appended to the system prompt, never the user message, so
    it cannot alter the content the deck must faithfully reproduce.

    Streams the response and collects it with ``get_final_message()`` so the
    large ``max_tokens`` does not hit the SDK's non-streaming timeout guard.

    ``timeout_s`` bounds ONE attempt's read and ``attempts`` says how many it
    gets; both apply only to a client this function builds, since an injected
    ``client`` carries its caller's own transport policy and it is not this
    function's to override. A stalled stream is retried once and logged, and a
    leg that exhausts its attempts raises ``model_call.ModelCallError``, whose
    message names the bound and the attempt count. See ``model_call``.
    """
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPTS.get(deck_type, SYSTEM_PROMPT)

    if preferences:
        system_prompt = f"{system_prompt}\n\n{preferences}"

    if client is None:
        import model_call  # lazy: keeps the pure pipeline dependency-free

        client = model_call.bounded_client(api_key, timeout_s=timeout_s)
    else:
        # An injected client is the caller's transport, so it keeps the caller's
        # bound and gets one attempt. Every test in this repo lands here, which
        # is why no test's call count moved when the retry arrived.
        timeout_s, attempts = None, 1

    # Strip the house-style header if the saved Design prompt carried one: this
    # leg gets the house style through the system prompt, so its user message is
    # the bare content prompt. A header-less prompt passes through unchanged.
    user_content = strip_house_style_brief(prompt)

    def send():
        # The whole request is rebuilt per attempt on purpose: a stream cannot be
        # replayed, so a retry has to open a new one.
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            return stream.get_final_message()

    import model_call  # lazy, and free: no SDK import happens on this path

    message = model_call.attempt("render", send, attempts=attempts,
                                 timeout_s=timeout_s)

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
    _check_complete(message, text, max_tokens)
    # Post-process, in order: strip any stray markdown fence, run the house text
    # gate (the deterministic anti-AI-voice pass: no em dashes, no in-paragraph
    # bold, no exclamation points, no banned vocabulary, colons flagged),
    # guarantee the logo asset, then stamp each slide with its 1-based
    # `data-slide` order. The stamp gives the deck a stable per-slide identifier
    # so a reviewer's in-UI edit (html_edit_layer) can address one slide
    # deterministically; it is a no-op on a document with no slide sections, so
    # the render contract is unchanged.
    #
    # The gate runs against `user_content`, the bare content prompt, so every
    # value the fidelity guard checks is masked out of its reach.
    return stamp_slide_ids(
        _ensure_logo_css(
            _apply_house_text_gate(_strip_code_fences(text), user_content, deck_type)
        )
    )
