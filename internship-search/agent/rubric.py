"""Reads rubric.md. The rubric itself is never in this file.

CLAUDE.md rule 3 is absolute: no scoring criterion, tier definition, or fit rule
appears in Python. This module knows only the shape of the file, never its
content. Every number the ranker applies comes out of the settings block in
rubric.md, so changing a tier boundary is an edit to that file and nothing else.

Two things come out of rubric.md and they have different audiences.

`prompt_text` is everything below the horizontal rule, sent to the model
verbatim, same convention as sources/intake_prompt.md. The note above the rule
is the owner writing to himself and must never reach the model.

`settings` is the single TOML block inside the file, parsed here and applied in
code after the model has answered. The model reports fit and reach; tier,
the REACH flag, and which model a posting is worth are derived from these.

Milestone 6 builds the ranker on top of this. Nothing here makes a model call.
"""

import re
import tomllib

from . import config

_TEXT: str | None = None
_SETTINGS: dict | None = None


class RubricError(RuntimeError):
    """rubric.md is missing, malformed, or internally inconsistent."""


def _read(path=None) -> str:
    path = path or config.RUBRIC_PATH
    try:
        return path.read_text()
    except FileNotFoundError as exc:
        raise RubricError(f"rubric.md not found at {path}") from exc


def prompt_text(path=None) -> str:
    """The instruction text below the horizontal rule in rubric.md.

    Everything above the rule is a note to the owner about how to edit the file
    and must not be billed for or acted on by the model.
    """
    global _TEXT
    if _TEXT is None or path is not None:
        _, _, body = _read(path).partition("\n---\n")
        text = (body or _read(path)).strip()
        if path is not None:
            return text
        _TEXT = text
    return _TEXT


def _extract_toml(text: str) -> str:
    """The one fenced TOML block in rubric.md.

    Exactly one is expected. Two would be ambiguous about which the ranker
    obeys, and silently obeying the first is the kind of bug that shows up as
    nothing at all, so it is an error instead.
    """
    blocks, current = [], None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if current is None:
                current = [] if line.strip() in ("```toml", "```TOML") else None
                if current is None:
                    continue
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)

    if not blocks:
        raise RubricError("rubric.md has no ```toml settings block")
    if len(blocks) > 1:
        raise RubricError(
            f"rubric.md has {len(blocks)} ```toml blocks; there must be exactly one"
        )
    return blocks[0]


def settings(path=None) -> dict:
    """The parsed settings block, validated. Cached, like the prefilter rules."""
    global _SETTINGS
    if _SETTINGS is None or path is not None:
        try:
            parsed = tomllib.loads(_extract_toml(_read(path)))
        except tomllib.TOMLDecodeError as exc:
            raise RubricError(f"settings block in rubric.md is not valid TOML: {exc}") from exc
        problems = validate(parsed)
        if problems:
            raise RubricError("rubric.md settings are inconsistent: " + "; ".join(problems))
        if path is not None:
            return parsed
        _SETTINGS = parsed
    return _SETTINGS


def tiers(cfg: dict) -> list[dict]:
    """Tier bands, highest fit requirement first, which is match order."""
    return sorted(cfg.get("tier", []), key=lambda t: t.get("min_fit", 0), reverse=True)


def validate(cfg: dict) -> list[str]:
    """Every way the settings block can be wrong, in plain English.

    A gap between two bands means a posting scores and lands in no tier at all,
    which is a coverage miss and therefore worth failing loudly over. Run this
    from tools/rubric_check.py after any edit.
    """
    problems: list[str] = []

    scores = cfg.get("scores") or {}
    lo, hi = scores.get("min"), scores.get("max")
    if not isinstance(lo, int) or not isinstance(hi, int):
        return ["[scores] needs integer min and max"]
    if lo >= hi:
        problems.append(f"[scores] min {lo} is not below max {hi}")

    bands = tiers(cfg)
    if not bands:
        problems.append("no [[tier]] bands defined")
    else:
        numbers = [b.get("number") for b in bands]
        if len(set(numbers)) != len(numbers):
            problems.append(f"tier numbers repeat: {numbers}")
        seen = set()
        for band in bands:
            fit = band.get("min_fit")
            if not isinstance(fit, int):
                problems.append(f"tier {band.get('number')} has no integer min_fit")
                continue
            if not lo <= fit <= hi:
                problems.append(
                    f"tier {band.get('number')} min_fit {fit} is outside {lo} to {hi}"
                )
            if fit in seen:
                problems.append(f"two tiers both start at fit {fit}")
            seen.add(fit)
            if band.get("delivery") not in ("daily", "sunday", "never"):
                problems.append(
                    f"tier {band.get('number')} delivery "
                    f"{band.get('delivery')!r} is not daily, sunday or never"
                )
        lowest = bands[-1].get("min_fit")
        if isinstance(lowest, int) and lowest > lo:
            problems.append(
                f"lowest tier starts at fit {lowest}, so a fit of {lo} lands in no tier"
            )

    reach = (cfg.get("reach") or {}).get("flag_at_or_below")
    if not isinstance(reach, int) or not lo <= reach <= hi:
        problems.append(f"[reach] flag_at_or_below must be an integer in {lo} to {hi}")

    routing = cfg.get("routing") or {}
    combined = routing.get("stage_c_min_combined")
    min_fit = routing.get("stage_c_min_fit")
    if not isinstance(combined, int) or not 2 * lo <= combined <= 2 * hi:
        problems.append(
            f"[routing] stage_c_min_combined must be an integer in {2 * lo} to {2 * hi}"
        )
    if not isinstance(min_fit, int) or not lo <= min_fit <= hi:
        problems.append(f"[routing] stage_c_min_fit must be an integer in {lo} to {hi}")

    variance = cfg.get("variance") or {}
    fraction = variance.get("sample_fraction")
    if not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
        problems.append("[variance] sample_fraction must be between 0 and 1")
    delta = variance.get("max_mean_delta")
    if not isinstance(delta, (int, float)) or delta < 0:
        problems.append("[variance] max_mean_delta must be zero or more")

    per_label = (cfg.get("few_shot") or {}).get("per_label")
    if not isinstance(per_label, int) or per_label < 0:
        problems.append("[few_shot] per_label must be zero or more")

    return problems


def tier_for(fit: int, cfg: dict | None = None) -> dict:
    """The tier band a fit score falls into. Highest band that it clears."""
    cfg = cfg or settings()
    for band in tiers(cfg):
        if fit >= band["min_fit"]:
            return band
    # Unreachable while validate() passes, which requires the lowest band to
    # reach scores.min. Kept so a hand-edited file fails loudly, not silently.
    raise RubricError(f"fit {fit} falls into no tier band")


def capped_tier(tier: int, category: str, title: str, cfg: dict | None = None) -> int:
    """The tier after every `[[tier_cap]]` in rubric.md that applies.

    Lower numbers are better tiers, so a cap raises the number. A title naming
    any `unless_title` entry is exempt. Short entries match as whole words and
    longer ones as a word-start prefix, which keeps "ai" off "aircraft".
    """
    cfg = cfg or settings()
    lowered = (title or "").lower()

    def names(words) -> bool:
        for word in words or []:
            w = re.escape(word.lower())
            pattern = rf"\b{w}\b" if len(word) <= 3 else rf"\b{w}"
            if re.search(pattern, lowered):
                return True
        return False

    for cap in cfg.get("tier_cap", []) or []:
        # A cap names an employer category, title words, or both, and applies
        # only where everything it names matches. One naming neither applies to
        # nothing, so a half-written entry can never cap the whole queue.
        if "category" not in cap and "title_has" not in cap:
            continue
        if "category" in cap and (not category or cap["category"] != category):
            continue
        if "title_has" in cap and not names(cap["title_has"]):
            continue
        if names(cap.get("unless_title")):
            continue
        tier = max(tier, int(cap.get("best_tier", tier)))
    return tier


def delivery_for(tier: int, cfg: dict | None = None) -> str:
    """Which email a scored posting belongs in: daily, sunday, or never.

    Read off the tier band rather than decided in the email code, because a tier
    and where it is delivered are one statement about how much a posting is
    worth. Moving tier 3 into the daily digest, or tier 4 into the Sunday
    roundup, is an edit to rubric.md and no code change. CLAUDE.md rule 3.
    """
    cfg = cfg or settings()
    for band in tiers(cfg):
        if band.get("number") == tier:
            return band.get("delivery", "never")
    # A tier number the rubric does not define. Surfaced rather than dropped, on
    # the same asymmetry the prefilter uses: a false surface costs a line, a
    # false silence costs the posting.
    return "daily"


def tiers_with_delivery(delivery: str, cfg: dict | None = None) -> list[int]:
    """Every tier number whose band routes to one email. Ordered best first."""
    cfg = cfg or settings()
    return [
        b["number"]
        for b in sorted(tiers(cfg), key=lambda t: t.get("number", 0))
        if b.get("delivery") == delivery
    ]


def is_reach(reach: int, cfg: dict | None = None) -> bool:
    """Whether this posting carries the REACH flag in the email."""
    cfg = cfg or settings()
    return reach <= cfg["reach"]["flag_at_or_below"]


def needs_stage_c(fit: int, reach: int, cfg: dict | None = None) -> bool:
    """Whether this posting is worth the expensive model for a final score."""
    cfg = cfg or settings()
    routing = cfg["routing"]
    return fit + reach >= routing["stage_c_min_combined"] or fit >= routing["stage_c_min_fit"]
