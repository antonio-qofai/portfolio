"""Weekly digest renderer.

Takes a week's scheduled chores and the house rules, returns a subject line
and a plain text body. Pure: no clock, no network, no templates on disk.
Every word it can say comes from the copy object it is handed, so this file
contains no English and no chore, room, or person name.

One shared body goes to everyone. Seeing the whole week's split is the point:
the fairness argument is only convincing if it is visible. Nothing here
reports on anybody. There is no completion state in the digest and no mention
of what is late, because overdue work is handled privately by nudges and a
shared email is the wrong place to make someone visibly behind.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    """One house rule, as it appears in the digest.

    category is a key into the category registry. number is optional and is
    displayed as given rather than recalculated, so the numbering people
    refer to out loud stays stable when rules are added or reordered.

    active_from and active_until are optional dates. A rule is included in a
    week's digest if it is active at any point during that week, so a rule
    that switches on midweek appears in that Monday's digest rather than the
    following one.
    """

    text: str
    category: str
    number: object = None
    active_from: object = None
    active_until: object = None


@dataclass(frozen=True)
class Digest:
    """A rendered email, ready for the sender to put in an envelope."""

    subject: str
    body: str


def render(week, scheduled, roster, rules, copy, categories):
    """Return the digest for one week."""
    _validate(week, scheduled, roster)
    sections = [_heading(week, scheduled, copy)]

    cleaner = _cleaner_note(scheduled, copy)
    if cleaner:
        sections.append(cleaner)

    sections.append(_chores(scheduled, roster, copy))

    rules_section = _rules(rules, week, copy, categories)
    if rules_section:
        sections.append(rules_section)

    if copy.footer.strip():
        sections.append(copy.footer)

    return Digest(subject=_subject(week, copy), body="\n\n".join(sections) + "\n")


def active_rules(rules, week, categories):
    """Return the rules in force during the week, in reading order.

    A rule counts as in force if its window overlaps the week at all.

    Within a category, rules come out in ascending number. Numbers are still
    displayed exactly as given and are never recalculated; this only decides
    the order they are printed in. A digest that lists rule 6 between 1 and 2
    reads like a mistake, and the numbers are how people refer to these out
    loud, so the printed order has to match them.

    Unnumbered rules follow the numbered ones, in the order they were given.
    The sort is stable, so anything the key does not distinguish keeps its
    original position.
    """
    live = []
    for rule in rules:
        _validate_rule(rule, categories)
        if rule.active_from is not None and rule.active_from > week.end_date:
            continue
        if rule.active_until is not None and rule.active_until < week.start_date:
            continue
        live.append(rule)
    return tuple(sorted(live, key=lambda rule: _rule_order(rule, categories)))


def _rule_order(rule, categories):
    """Sort key: category first, then number, then unnumbered rules last."""
    return (
        categories[rule.category].order,
        rule.number is None,
        rule.number if rule.number is not None else 0,
    )


def format_due(moment, copy):
    """Return a due datetime as the digest writes it."""
    hour = moment.hour % 12 or 12
    return copy.due_format.format(
        weekday=copy.weekday_names[moment.weekday()],
        month=copy.month_names[moment.month - 1],
        day=moment.day,
        hour=hour,
        minute="" if moment.minute == 0 else ":%02d" % moment.minute,
        meridiem=copy.am if moment.hour < 12 else copy.pm,
    )


def format_date(day, copy):
    """Return a date as the digest writes it."""
    return copy.date_format.format(
        weekday=copy.weekday_names[day.weekday()],
        month=copy.month_names[day.month - 1],
        day=day.day,
    )


def _subject(week, copy):
    return copy.subject.format(
        number=week.number,
        start=_short_date(week.start_date, copy),
        end=_short_date(week.end_date, copy),
    )


def _heading(week, scheduled, copy):
    """The week, and the one deadline that covers most of it."""
    lines = [_header(week, copy)]
    note = _due_note(scheduled, copy)
    if note:
        lines.append(note)
    return "\n".join(lines)


def _due_note(scheduled, copy):
    """State the deadline once rather than on every chore line.

    Every ordinary chore in a week falls due at the same moment, so
    repeating it eight times is noise that makes the list harder to scan,
    not easier. Prep chores in a cleaner week have their own deadline,
    which the cleaner note states.
    """
    ordinary = [item for item in scheduled if not item.is_prep]
    if not ordinary or not copy.due_note.strip():
        return ""
    return copy.due_note.format(due=format_due(ordinary[0].due, copy))


def _header(week, copy):
    return copy.header.format(
        number=week.number,
        start=_short_date(week.start_date, copy),
        end=_short_date(week.end_date, copy),
    )


def _short_date(day, copy):
    return copy.range_format.format(
        weekday=copy.weekday_names[day.weekday()],
        month=copy.month_names[day.month - 1],
        day=day.day,
    )


def _cleaner_note(scheduled, copy):
    """Return the cleaner line, or empty if no prep is happening this week."""
    prep = [item for item in scheduled if item.is_prep]
    if not prep:
        return ""
    visit = prep[0].cleaner_visit
    return copy.cleaner_note.format(
        date=format_date(visit.visit_date, copy),
        due=format_due(prep[0].due, copy),
    )


def _chores(scheduled, roster, copy):
    if not scheduled:
        return copy.no_chores

    # One block per person, blank line between them. Three names running
    # together is the difference between scanning for your own and reading
    # the whole thing.
    blocks = []
    for person in roster:
        lines = [copy.person_heading.format(person=person)]
        mine = sorted(
            (item for item in scheduled if item.assignee == person),
            key=lambda item: (item.due, item.name),
        )
        if not mine:
            lines.append(copy.nothing_for_person)
        for item in mine:
            template = copy.prep_chore_line if item.is_prep else copy.chore_line
            lines.append(
                template.format(
                    name=item.name, task=item.task, due=format_due(item.due, copy)
                )
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _rules(rules, week, copy, categories):
    live = active_rules(rules, week, categories)
    if not live:
        return ""

    blocks = [copy.rules_heading]
    current = None
    for rule in live:
        category = categories[rule.category]
        if category.name != current:
            blocks.append("")
            blocks.append(category.heading)
            current = category.name
        if rule.number is None:
            blocks.append(copy.rule_unnumbered.format(text=rule.text))
        else:
            blocks.append(
                copy.rule_numbered.format(number=rule.number, text=rule.text)
            )
    return "\n".join(blocks)


def _validate(week, scheduled, roster):
    if len(roster) == 0:
        raise ValueError("roster is empty; there is nobody to send a digest to")
    for item in scheduled:
        if item.week.number != week.number:
            raise ValueError(
                "chore %r belongs to week %d but the digest is for week %d"
                % (item.name, item.week.number, week.number)
            )
        if item.assignee not in roster:
            raise ValueError(
                "chore %r is assigned to %r, who is not on the roster %s"
                % (item.name, item.assignee, list(roster))
            )


def _validate_rule(rule, categories):
    if not rule.text.strip():
        raise ValueError("a house rule has empty text")
    if rule.category not in categories:
        raise ValueError(
            "house rule %r has unknown category %r; known categories are %s"
            % (rule.text, rule.category, sorted(categories))
        )
    if (
        rule.active_from is not None
        and rule.active_until is not None
        and rule.active_until < rule.active_from
    ):
        raise ValueError(
            "house rule %r ends (%s) before it starts (%s)"
            % (rule.text, rule.active_until, rule.active_from)
        )


# --------------------------------------------------------------------- #
# HTML rendering
#
# A second view of the same digest, for clients that will render it. The
# plain text version above is still built and still sent alongside, both
# because it is what a phone lock screen shows and because an HTML-only
# message is far likelier to be filed as Promotions.
#
# Every word still comes from the copy object. Only the presentation comes
# from the style object, so the two versions cannot drift apart.
# --------------------------------------------------------------------- #

import html as _html


def render_html(week, scheduled, roster, rules, copy, categories, style):
    """Return the HTML body for one week's digest."""
    _validate(week, scheduled, roster)

    parts = [_html_header(week, scheduled, copy, style)]

    cleaner = _cleaner_note(scheduled, copy)
    if cleaner:
        parts.append(_html_callout(cleaner, style))

    parts.append(_html_chores(scheduled, roster, copy, style))

    rules_html = _html_rules(rules, week, copy, categories, style)
    if rules_html:
        parts.append(_html_divider(style))
        parts.append(rules_html)

    if copy.footer.strip():
        parts.append(_html_divider(style))
        parts.append(_html_footer(copy, style))

    return _html_shell("".join(parts), style)


def _html_shell(body, style):
    return (
        '<div style="margin:0;padding:24px 12px;background:{page};'
        'font-family:{font};">'
        '<div style="max-width:{w}px;margin:0 auto;background:{card};'
        'border:1px solid {border};padding:30px 28px;">{body}</div>'
        '</div>'
    ).format(
        page=style.page_bg, font=style.font, w=style.width_px,
        card=style.card_bg, border=style.border, body=body,
    )


def _html_header(week, scheduled, copy, style):
    out = (
        '<div style="font-family:{font};font-size:23px;font-weight:bold;'
        'color:{text};line-height:1.25;">{title}</div>'
    ).format(font=style.font, text=style.text, title=_e(_header(week, copy)))
    note = _due_note(scheduled, copy)
    if note:
        out += (
            '<div style="font-family:{font};font-size:15px;color:{muted};'
            'margin-top:6px;">{note}</div>'
        ).format(font=style.font, muted=style.muted, note=_e(note))
    return out + _html_divider(style)


def _html_divider(style):
    return (
        '<div style="border-top:1px solid {border};margin:20px 0;"></div>'
    ).format(border=style.border)


def _html_callout(text, style):
    """The cleaner note. The one thing in a week that is not routine."""
    return (
        '<div style="font-family:{font};font-size:15px;line-height:1.5;'
        'color:{text};background:{bg};border-left:3px solid {accent};'
        'padding:11px 13px;margin-bottom:20px;">{body}</div>'
    ).format(
        font=style.font, text=style.text, bg=style.accent_bg,
        accent=style.accent, body=_e(text),
    )


def _html_chores(scheduled, roster, copy, style):
    if not scheduled:
        return (
            '<div style="font-family:{font};font-size:16px;color:{muted};">'
            '{text}</div>'
        ).format(font=style.font, muted=style.muted, text=_e(copy.no_chores))

    blocks = []
    for person in roster:
        heading = (
            '<div style="font-family:{font};font-size:17px;font-weight:bold;'
            'color:{text};margin-bottom:4px;">{name}</div>'
        ).format(
            font=style.font, text=style.text,
            name=_e(copy.person_heading.format(person=person)),
        )
        mine = sorted(
            (item for item in scheduled if item.assignee == person),
            key=lambda item: (item.due, item.name),
        )
        if not mine:
            rows = (
                '<div style="font-family:{font};font-size:16px;color:{muted};'
                'font-style:italic;">{text}</div>'
            ).format(
                font=style.font, muted=style.muted,
                text=_e(copy.nothing_for_person.strip(" -")),
            )
        else:
            rows = "".join(_html_chore(item, copy, style) for item in mine)
        blocks.append(
            '<div style="margin-bottom:18px;">%s%s</div>' % (heading, rows)
        )
    return "".join(blocks)


def _html_chore(item, copy, style):
    line = (
        '<div style="font-family:{font};font-size:16px;line-height:1.65;'
        'color:{text};">&bull;&nbsp;{name}'
    ).format(font=style.font, text=style.text, name=_e(item.name))
    if item.is_prep:
        line += (
            '<span style="color:{accent};font-style:italic;">'
            '&nbsp;&mdash;&nbsp;{note}</span>'
        ).format(
            accent=style.accent,
            note=_e(copy.prep_note.format(task=item.task)),
        )
    return line + "</div>"


def _html_rules(rules, week, copy, categories, style):
    live = active_rules(rules, week, categories)
    if not live:
        return ""

    out = (
        '<div style="font-family:{font};font-size:12px;font-weight:bold;'
        'letter-spacing:0.09em;text-transform:uppercase;color:{muted};'
        'margin-bottom:12px;">{heading}</div>'
    ).format(font=style.font, muted=style.muted, heading=_e(copy.rules_heading))

    current = None
    for rule in live:
        category = categories[rule.category]
        if category.name != current:
            out += (
                '<div style="font-family:{font};font-size:14px;'
                'font-style:italic;color:{text};margin:14px 0 6px;">'
                '{heading}</div>'
            ).format(
                font=style.font, text=style.text, heading=_e(category.heading)
            )
            current = category.name
        # Numbers are printed as given, never recalculated, so this cannot
        # be an <ol> — the list is filtered by activation date and an <ol>
        # would silently renumber whatever survives.
        label = "&bull;" if rule.number is None else "%s." % _e(rule.number)
        out += (
            '<div style="font-family:{font};font-size:14px;line-height:1.55;'
            'color:{muted};padding-left:18px;text-indent:-18px;">'
            '{label}&nbsp;{text}</div>'
        ).format(
            font=style.font, muted=style.muted, label=label, text=_e(rule.text)
        )
    return out


def _html_footer(copy, style):
    lines = []
    for line in copy.footer.strip().splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            lines.append(
                '<a href="{url}" style="color:{accent};">{url}</a>'
                .format(url=_e(line), accent=style.accent)
            )
        else:
            lines.append(_e(line))
    return (
        '<div style="font-family:{font};font-size:13px;line-height:1.6;'
        'color:{muted};">{body}</div>'
    ).format(font=style.font, muted=style.muted, body="<br>".join(lines))


def _e(value):
    """Escape for HTML. Airtable is hand-edited and contains & and quotes."""
    return _html.escape(str(value), quote=True)
