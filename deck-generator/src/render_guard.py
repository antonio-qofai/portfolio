"""Render-fidelity guard — no prompt value silently lost between prompt and HTML.

The failure this exists to catch: the Deck Renderer (`src/deck_renderer.py`)
hands the assembled prompt to Claude with a system prompt that says "render
every field faithfully and completely; do not invent, omit, summarize, or
reword" and "preserve the reviewer markers verbatim and visibly." Claude is
free to generate the HTML however it likes, so nothing forces it to keep that
promise. The field-coverage guard (`src/coverage_guard.py`) proves the
packet-to-prompt boundary is drop-free; this module is the analogous check one
boundary downstream, the prompt-to-HTML boundary (PRD criterion 13). It runs
after the HTML is generated and before anything is written to disk.

Unlike the coverage guard, this is not an exact structural walk: the prompt is
free-form text and the HTML is freely designed, so there is no schema to
partition against. Instead it extracts the high-signal tokens a design prompt
carries — dollar amounts, percentages, multipliers (footnote figures are just
dollar/percent/multiplier tokens embedded in `terms_footnote`), week ranges,
scenario names, and milestone ids/labels — and checks each survived into the
rendered HTML as a normalized substring. Long prose copy (headlines, summaries,
descriptions) is not checked here; a reworded sentence is a real risk this
module does not try to catch, per the design brief's "best-effort" framing.

Two different enforcement levels, because the two things being checked carry
different false-positive risk:

- Reviewer markers (`[MISSING: role]`, `(unconfirmed, see gaps)`) are fixed
  strings the system prompt tells Claude to preserve verbatim. A miss here is
  unambiguous, so a missing marker is always a hard failure
  (``RenderFidelityError``), regardless of ``strict``.
- Load-bearing values (dollar amounts, percentages, etc.) can legitimately be
  reformatted by Claude without being lost (`$1,500,000` rendered as `$1.5M`
  is arguably fine, not a drop) even though exact-substring matching would
  flag it. So a missing value only goes into the returned report by default;
  pass ``strict=True`` to also raise on a missing value.

SECTION LABELS ARE THE SECOND KIND AND NOT THE FIRST, which is a judgement
rather than an obvious placement. A label is a fixed string the system prompt
binds to a role, so a miss is meaningful in a way a reworded headline is not,
and the case for raising on it is real. What rules it out is the false-positive
cost: the label is a composed two-part string, Claude may legitimately split it
across elements or set the separator differently, and this leg costs about four
minutes, so raising would throw a render away over a reformatting. Reporting is
what turns "nothing can see whether the label survived" into something a
reviewer reads, and ``strict=True`` raises on it with everything else.

Presence, not count: a marker or value found once in the prompt only needs to
appear at least once in the HTML. Claude sometimes echoes a marker in more
than one place (e.g. both a heading badge and inline in the value text); that
is a bonus, not a requirement, so occurrence counts are never compared.
"""

import html
import re

# Dash variants Claude may swap for each other (hyphen-minus, non-breaking
# hyphen, figure/en/em dash, minus sign) are normalized to a plain hyphen
# before any comparison, so a reformatted dash is never a false positive.
_DASH_CHARS = "‐‑‒–—−-"
_DASH_RE = re.compile(f"[{re.escape(_DASH_CHARS)}]")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

MISSING_MARKER_RE = re.compile(r"\[MISSING: [^\]]+\]")
UNCONFIRMED_MARKER = "(unconfirmed, see gaps)"

# A slide's own section label, read off the prompt by the key it arrives under
# rather than from a list of role names, so both deck types are covered and a
# further label is covered the day a template declares one. The status deck has
# carried three of these since it shipped; the proposal deck's opportunity slide
# gained one on 2026-09-15, when a deck that can hold several opportunities
# needed its slides to say which one they are.
#
# WHY IT IS CHECKED AT ALL. Until that change the proposal deck's section labels
# were never SUPPLIED: the template's `Section label:` line is prose the loader
# does not read and the assembler does not emit, so every one of them was the
# model's own invention from the slide title. That works while a label is a
# fixed string and stops working the moment it has to say WHICH opportunity a
# slide is, because an invented label cannot know.
SECTION_LABEL_RE = re.compile(r"^[A-Za-z0-9_]*section_label: (.+)$", re.MULTILINE)

# [ \t]* rather than \s*, deliberately: \s matches newlines, and the prompt's
# "key: value" line format packs unrelated fields tightly (e.g. a record's
# `number: 04` immediately above `week: WK 1-2`) — a newline-crossing \s* was
# observed to splice adjacent lines into a bogus token ("04\n  week"). Every
# token these regexes extract is meant to sit on one line.
_SIGN = f"[+{re.escape(_DASH_CHARS)}]?"
DOLLAR_RE = re.compile(r"\$\d[\d,]*(?:\.\d+)?(?:[ \t]*[KMB])?")
PERCENT_RE = re.compile(
    rf"{_SIGN}\d+(?:\.\d+)?"
    rf"(?:[ \t]*[{re.escape(_DASH_CHARS)}][ \t]*{_SIGN}\d+(?:\.\d+)?)?"
    r"[ \t]*(?:pp|%)"
)
MULTIPLIER_RE = re.compile(r"\d+(?:\.\d+)?[ \t]*×")
_WEEK_FORWARD_RE = re.compile(
    rf"\b(?:WKS?|WEEKS?)[ \t]*\d+(?:[ \t]*[{re.escape(_DASH_CHARS)}][ \t]*\d+)?\b",
    re.IGNORECASE,
)
_WEEK_REVERSE_RE = re.compile(
    r"\b\d+[ \t]*-?[ \t]*(?:WKS?|WEEKS?)\b", re.IGNORECASE
)

# Structural extraction: these field names are only used by the proposal
# template's milestones / value_mapping records today (Module 1's declared
# record fields), so a plain "key: value" line match is unambiguous. This
# ties the extractor to the current template's record field names rather than
# being a general schema-aware parser — a deliberate scope choice, documented
# rather than hidden, matching this module's "pure string processing" brief.
_SCENARIO_LINE_RE = re.compile(r"^[ \t]*scenario:[ \t]*(.+)$", re.MULTILINE)
_MILESTONE_ID_LINE_RE = re.compile(r"^[ \t]*id:[ \t]*(.+)$", re.MULTILINE)
_MILESTONE_LABEL_LINE_RE = re.compile(r"^[ \t]*label:[ \t]*(.+)$", re.MULTILINE)

# Status-path structural extraction: the load-bearing time-aware tokens a status
# prompt carries that the generic numeric extractors do not — the TODAY marker's
# column id and label and the "Week N of M" line (PRD S4/S13). Tied to the status
# template's role names the same documented way the proposal extractors are tied
# to its record fields.
_TODAY_WEEK_LINE_RE = re.compile(r"^[ \t]*today_marker_week:[ \t]*(.+)$", re.MULTILINE)
_TODAY_LABEL_LINE_RE = re.compile(r"^[ \t]*today_marker_label:[ \t]*(.+)$", re.MULTILINE)
_PROJECT_WEEK_LINE_RE = re.compile(r"^[ \t]*project_week:[ \t]*(.+)$", re.MULTILINE)


class RenderFidelityError(AssertionError):
    """A prompt-to-HTML fidelity check failed.

    Subclasses ``AssertionError`` so it fails as loudly as a broken invariant
    and the lightweight test runners in ``tests/`` (which catch
    ``AssertionError``) report it as a clean failure. Always raised for a
    dropped reviewer marker; raised for a dropped value only when the caller
    opts into ``strict=True``.
    """


def _normalize(text):
    """Strip tags, unescape entities, fold dash variants, collapse whitespace,
    and casefold — so a stray `&amp;`, an inline `<strong>`, a swapped dash, or
    a case change (e.g. CSS `text-transform: uppercase`) is never mistaken for
    a dropped value.

    Order matters: strip tags BEFORE unescaping. A value like `<15%` is rendered
    as the entity `&lt;15%`; unescaping first would turn it back into `<15%`,
    and the tag stripper (`<[^>]+>`) would then read that `<` as the start of a
    tag and delete `15%` along with everything up to the next real `>` — the
    value would vanish and read as a spurious drop. Stripping real tags first
    leaves `&lt;15%` untouched (it is not a tag), and unescaping after safely
    restores `<15%` with nothing left to eat it."""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _DASH_RE.sub("-", text)
    text = _WS_RE.sub(" ", text)
    return text.strip().casefold()


def _extract_markers(prompt):
    """Every distinct reviewer marker string present in the prompt."""
    markers = set(MISSING_MARKER_RE.findall(prompt))
    if UNCONFIRMED_MARKER in prompt:
        markers.add(UNCONFIRMED_MARKER)
    return markers


def _extract_values(prompt, deck_type="proposal"):
    """High-signal load-bearing tokens from the prompt, grouped by category.

    Dollar amounts, percentages, and multipliers are extracted globally (a
    `$`, `%`/`pp`, or `×` is unambiguous regardless of surrounding
    context — this is what catches the composed EV footnote's figures too,
    since `terms_footnote` is prose containing exactly these token shapes).
    Week ranges match both orders Claude might encounter (`WKS 1–8` and
    `16-WEEK`). These generic numeric categories apply to both deck types.

    The structural categories are deck-type specific, extracted from the
    prompt's own `key: value` line format: on the proposal path, scenario names
    and milestone ids/labels; on the status path, the TODAY marker's column id
    and label and the "Week N of M" line (PRD S4/S13).
    """
    week_ranges = set(_WEEK_FORWARD_RE.findall(prompt)) | set(
        _WEEK_REVERSE_RE.findall(prompt)
    )
    values = {
        "dollar_amounts": sorted(set(DOLLAR_RE.findall(prompt))),
        "percentages": sorted({m.strip() for m in PERCENT_RE.findall(prompt)}),
        "multipliers": sorted(set(MULTIPLIER_RE.findall(prompt))),
        "week_ranges": sorted(week_ranges),
    }
    if deck_type == "status":
        values["today_marker"] = sorted(
            {m.strip() for m in _TODAY_WEEK_LINE_RE.findall(prompt)}
            | {m.strip() for m in _TODAY_LABEL_LINE_RE.findall(prompt)}
        )
        values["project_week"] = sorted(
            {m.strip() for m in _PROJECT_WEEK_LINE_RE.findall(prompt)}
        )
    else:
        values["scenario_names"] = sorted(
            {m.strip() for m in _SCENARIO_LINE_RE.findall(prompt)}
        )
        values["milestone_ids"] = sorted(
            {m.strip() for m in _MILESTONE_ID_LINE_RE.findall(prompt)}
        )
        values["milestone_labels"] = sorted(
            {m.strip() for m in _MILESTONE_LABEL_LINE_RE.findall(prompt)}
        )
    return values


def protected_strings(prompt, deck_type="proposal"):
    """Every load-bearing token this guard checks, as a flat list of literals.

    The house text gate (``text_gate``) masks these before it rewrites a word, so
    no value this guard is responsible for is even visible to that pass. That
    keeps one invariant true in both directions: what the fidelity guard checks,
    the text gate cannot touch.

    Same extractors, same deck-type split as :func:`check_render_fidelity`, so the
    two can never drift apart.
    """
    out = []
    for tokens in _extract_values(prompt, deck_type).values():
        out.extend(token for token in tokens if token and token.strip())
    return sorted(set(out))


def _missing_tokens(tokens, haystack, *, strip_commas=False):
    missing = []
    for token in tokens:
        needle = _normalize(token)
        hay = haystack
        if strip_commas:
            needle = needle.replace(",", "")
            hay = hay.replace(",", "")
        if needle not in hay:
            missing.append(token)
    return missing


def check_render_fidelity(prompt, rendered_html, *, strict=False, deck_type="proposal"):
    """Assert every reviewer marker, and (optionally) every load-bearing value,
    in ``prompt`` survives into ``rendered_html``.

    ``deck_type`` selects the structural value extractors so the guard covers the
    status prompt (its TODAY marker, project week, and the generic numeric
    tokens) the same way it covers the proposal prompt. The reviewer-marker check
    is identical for both.

    Always raises ``RenderFidelityError`` (naming the exact marker strings)
    when a `[MISSING: ...]` or `(unconfirmed, see gaps)` marker present in the
    prompt cannot be found in the HTML — low false-positive risk, since these
    are fixed strings Claude is told to preserve verbatim.

    Returns a report dict on success:
    ``{"ok": bool, "checked": {category: count, ...}, "missing_values": {category: [token, ...], ...}}``.
    A missing value does not raise unless ``strict=True`` — Claude may
    legitimately reformat a value (e.g. `$1,500,000` -> `$1.5M`) without
    actually dropping it, so exact-substring misses here are report-only by
    default and surfaced for human review rather than blocking the run.
    """
    normalized_html = _normalize(rendered_html)

    markers = _extract_markers(prompt)
    missing_markers = sorted(
        m for m in markers if _normalize(m) not in normalized_html
    )
    if missing_markers:
        listing = "\n".join(f"  - {m}" for m in missing_markers)
        raise RenderFidelityError(
            "reviewer marker(s) present in the prompt did not survive into "
            "the rendered HTML, so a human reviewer would see no signal of "
            "the gap:\n"
            f"{listing}"
        )

    values = _extract_values(prompt, deck_type)
    # The labels the prompt SUPPLIED, which is not all of them: a slide whose
    # label the system prompt never bound to a role has none here, and a role
    # that came through empty carries a marker the check above already holds.
    labels = [match.strip() for match in SECTION_LABEL_RE.findall(prompt)
              if match.strip() and not MISSING_MARKER_RE.match(match.strip())]
    if labels:
        values = dict(values, section_labels=labels)
    missing_values = {}
    for category, tokens in values.items():
        missing = _missing_tokens(
            tokens, normalized_html, strip_commas=(category == "dollar_amounts")
        )
        if missing:
            missing_values[category] = missing

    report = {
        "ok": not missing_values,
        "checked": {category: len(tokens) for category, tokens in values.items()},
        "missing_values": missing_values,
    }

    if strict and missing_values:
        listing = "\n".join(
            f"  - {category}: {tokens}" for category, tokens in missing_values.items()
        )
        raise RenderFidelityError(
            "load-bearing value(s) from the prompt did not survive into the "
            "rendered HTML (strict mode):\n"
            f"{listing}"
        )

    return report
