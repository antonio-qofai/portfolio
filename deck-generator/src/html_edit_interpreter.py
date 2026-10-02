"""Free-text edit interpreter — turn a reviewer's plain-language instruction into
deterministic find-and-replace edits on a rendered deck.

The review UI used to ask a reviewer to fill two fields: the exact text to find
and the exact text to replace it with. That was too much (Antonio, 2026-07-21).
This module backs a single free-text box instead: the reviewer types what they
want changed in their own words ("the revenue figure on slide 2 should be $42M",
"drop the Oxford comma in the closing line"), a Claude call reads the current
deck and translates that into one or more EXACT ``{slide, source, replacement}``
swaps, and those swaps are then applied by the existing deterministic edit layer
(``html_edit_layer.apply_edits_and_save``).

Content-only, no drift: the model's ONLY job here is to name exact substrings
already present on the deck and their replacements. It never rewrites the HTML,
never touches layout or styling, and never re-renders. The actual mutation is the
same exact-string swap a manual edit used, so the rest of the deck stays
byte-for-byte identical and the change stays in the audit log. An instruction the
model cannot express as concrete swaps comes back as ``unresolved`` text the UI
shows the reviewer, and nothing is changed.

Credentials and the injected-``client`` contract mirror ``deck_renderer``: the
``anthropic`` package is imported lazily so the pure pipeline stays
dependency-free, and a caller may inject any object exposing
``client.messages.stream(...)`` as a context manager with ``get_final_message()``
to test without a network call.
"""

import json
import re

# A quick, capable default for a structured find-and-replace task; overridable
# per call. Matches the renderer's tier so one key and one model family serve the
# whole tool.
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"

# One attempt's read bound, and how many attempts. This leg is not in the live
# proposal run that was measured on 2026-09-02 -- it fires when a reviewer asks
# for an edit on a deck that already exists -- so it is sized by its shape
# rather than by a stopwatch: 8k tokens at medium effort against one deck's
# HTML, which is a fraction of the render's 64k at high effort. 240 seconds
# gives it generous room and still puts a reviewer waiting on an edit in front
# of an answer, or an error, in four minutes rather than ten.
DEFAULT_TIMEOUT_S = 240.0
DEFAULT_ATTEMPTS = 2

SYSTEM_PROMPT = (
    "You translate a reviewer's plain-language edit request into exact "
    "find-and-replace operations on an already-rendered HTML slide deck. You do "
    "NOT rewrite the deck, change layout, styling, or CSS, add or remove slides, "
    "or invent facts. Your only output is a set of precise text swaps.\n\n"
    "You are given the full deck HTML. Each slide is a "
    "`<section class=\"slide ...\" data-slide=\"N\">` block; N is the slide number "
    "you must report.\n\n"
    "Return ONE JSON object and nothing else (no prose, no markdown fences):\n"
    "{\n"
    '  "edits": [\n'
    '    {"slide": <int>, "source": "<exact substring copied verbatim from that '
    'slide>", "replacement": "<the new text>", "note": "<short human summary>"}\n'
    "  ],\n"
    '  "unresolved": "<if the request cannot be done as text swaps, explain why '
    'here; otherwise omit or leave empty>"\n'
    "}\n\n"
    "RULES:\n"
    "- `source` MUST be an exact, unique substring of the visible text on that "
    "slide, copied character-for-character (including case and punctuation) so a "
    "literal string search finds exactly one occurrence on the slide. Prefer the "
    "shortest span that is still unique. Do not include HTML tags unless they are "
    "essential to make the match unique.\n"
    "- `replacement` replaces `source` exactly. If `source` includes surrounding "
    "HTML tags (e.g. `<span class=\"base\">...</span>`) to make the match unique, "
    "`replacement` MUST repeat those identical tags and change only the text "
    "between them — never drop or rewrite the tags, or they will show up as literal "
    "text on the slide. If `source` is plain text with no tags, `replacement` is "
    "plain text too. To delete text, use an empty replacement.\n"
    "- Only change wording, numbers, names, and punctuation (content edits). If "
    "the request is about size, color, spacing, position, or any visual style, do "
    "NOT attempt it: put an explanation in `unresolved` instead.\n"
    "- If one request implies several changes (e.g. the same figure in two "
    "places), return one edit per exact occurrence, each with its own unique "
    "`source`.\n"
    "- If you cannot find the text the reviewer means, return no edits and explain "
    "in `unresolved`."
)

# Heading for the standing-preferences block appended to the system prompt when a
# run carries active preferences. Framed for the EDIT context: preferences shape
# how a replacement is worded, never what content is added or removed.
PREFERENCES_HEADING = (
    "STANDING REVIEWER PREFERENCES (format-only refinements captured from prior "
    "human review). When you choose the `replacement` text for an edit, it MUST "
    "comply with every preference below. They constrain how a replacement is "
    "worded and formatted; they never authorize adding, dropping, or inventing "
    "content, and they never override the rules above. Apply each where relevant:"
)


def _system_prompt_with_preferences(preferences):
    """The system prompt, with a standing-preferences block appended when present.

    ``preferences`` is a list of note strings (empty/None means none). With no
    preferences the base ``SYSTEM_PROMPT`` is returned unchanged, so the
    no-preferences path stays byte-identical.
    """
    notes = [n.strip() for n in (preferences or []) if isinstance(n, str) and n.strip()]
    if not notes:
        return SYSTEM_PROMPT
    block = "\n".join([PREFERENCES_HEADING, *(f"- {n}" for n in notes)])
    return f"{SYSTEM_PROMPT}\n\n{block}"


def _strip_code_fences(text):
    """Drop a leading/trailing markdown code fence if the model added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(text):
    """Best-effort parse of the model's reply into a dict.

    Tries the whole (de-fenced) reply first, then falls back to the first
    balanced ``{...}`` span, so a stray sentence around the JSON does not break
    parsing. Raises ``ValueError`` when no JSON object can be recovered.
    """
    candidate = _strip_code_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError("could not parse an edit plan from the model response")


def _coerce_edits(payload):
    """Validate and normalize the model's JSON into a clean edit list.

    Returns ``(edits, unresolved)`` where ``edits`` is a list of
    ``{"slide": int, "source": str, "replacement": str, "note": str}`` and
    ``unresolved`` is the model's explanation string (``""`` when none). Silently
    drops malformed entries (missing slide/source) rather than trusting them.
    """
    if not isinstance(payload, dict):
        raise ValueError("edit plan was not a JSON object")
    edits = []
    for raw in payload.get("edits") or []:
        if not isinstance(raw, dict):
            continue
        try:
            slide = int(raw.get("slide"))
        except (TypeError, ValueError):
            continue
        source = raw.get("source")
        if not isinstance(source, str) or not source:
            continue
        replacement = raw.get("replacement")
        if not isinstance(replacement, str):
            replacement = "" if replacement is None else str(replacement)
        note = raw.get("note")
        edits.append({
            "slide": slide,
            "source": source,
            "replacement": replacement,
            "note": note if isinstance(note, str) else "",
        })
    unresolved = payload.get("unresolved")
    return edits, (unresolved.strip() if isinstance(unresolved, str) else "")


def interpret_edit(
    html,
    instruction,
    *,
    preferences=None,
    model=DEFAULT_MODEL,
    client=None,
    api_key=None,
    max_tokens=DEFAULT_MAX_TOKENS,
    effort=DEFAULT_EFFORT,
    timeout_s=DEFAULT_TIMEOUT_S,
    attempts=DEFAULT_ATTEMPTS,
):
    """Translate a free-text ``instruction`` into exact swaps on ``html``.

    Returns ``{"edits": [...], "unresolved": str}``. ``edits`` entries are
    ``{"slide", "source", "replacement", "note"}`` ready for
    ``html_edit_layer.apply_edits_and_save``; ``unresolved`` carries the model's
    explanation when it could not express the request as content swaps (empty
    otherwise). Raises ``ValueError`` on an empty instruction or an unparseable
    model reply.

    ``preferences`` is an optional list of standing reviewer-preference note
    strings (from ``preference_store.applicable_preferences``). When present, the
    interpreter must word every replacement to comply with them, so a reviewer's
    standing formatting rules shape edits just as they shape a fresh render. With
    no preferences the request is byte-identical to before.

    ``client`` may be injected (any object exposing ``messages.stream(...)`` as a
    context manager yielding ``get_final_message()``); when omitted a bounded
    ``anthropic.Anthropic`` client is built and reads ``ANTHROPIC_API_KEY`` (or
    the explicit ``api_key``). Mirrors ``deck_renderer.render_deck_html`` so the
    UI configures one client for both legs.

    ``timeout_s`` bounds one attempt's read and ``attempts`` says how many it
    gets, on a client this function builds; an injected ``client`` keeps its own
    transport and gets one attempt. See ``model_call``.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("no edit instruction was given")

    if client is None:
        import model_call  # lazy: keeps the pure pipeline dependency-free

        client = model_call.bounded_client(api_key, timeout_s=timeout_s)
    else:
        # The caller's transport, so the caller's bound and one attempt.
        timeout_s, attempts = None, 1

    user_content = (
        f"REVIEWER REQUEST:\n{instruction}\n\n"
        f"CURRENT DECK HTML:\n{html}"
    )

    def send():
        # Rebuilt per attempt: a stream cannot be replayed, so a retry opens one.
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=_system_prompt_with_preferences(preferences),
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            return stream.get_final_message()

    import model_call  # lazy, and free: no SDK import happens on this path

    message = model_call.attempt("edit interpreter", send, attempts=attempts,
                                 timeout_s=timeout_s)

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
    edits, unresolved = _coerce_edits(_extract_json_object(text))
    return {"edits": edits, "unresolved": unresolved}
