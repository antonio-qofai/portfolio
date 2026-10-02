"""Layout guard — no content clipped or pushed off a slide, on any deck.

The blind spot this closes. The other three guards all read the deck as a STRING:

- ``packet_consistency`` checks the packet against itself.
- ``coverage_guard`` checks packet-to-prompt for dropped fields.
- ``render_guard`` checks prompt-to-HTML for dropped values.

A clipped label defeats all three by construction. ``overflow:hidden`` hides text
at paint time, it does not remove it — the full string is still in the DOM, so a
substring search finds it and every guard reports clean while the deck on screen
reads "Switch pro". The loss exists only in rendered geometry, and nothing in the
pipeline measured geometry. That is how the proposal Gantt shipped truncated bar
labels for weeks (Antonio, 2026-07-24).

So this module renders the deck and MEASURES it. It drives the locally installed
Chrome in headless mode, injects a measuring script, and reports three defects:

1. CLIPPED — an element's own text overflows its box and the box clips
   (``scrollWidth > clientWidth`` on an element whose ``overflow`` is not
   ``visible``). This is the truncated-bar-label case.
2. ESCAPED — an element's box extends past the nearest clipping ancestor's
   padding box, so its content is cut at that container's edge. This is the
   bar-overflows-the-panel and content-off-the-slide case.
3. OVERLAPPED — two elements that each paint their own text intersect, with
   neither containing the other, so one set of words is painted over another.

The third was added 2026-08-19, after a deck shipped with slide 2's two panels
spilling 192px of bullet text across the build band below them while this guard
reported the deck clean. It was right by its own definition and blind by its own
construction: every box in that chain has ``overflow:visible``, so nothing was
clipped, and the spill stopped 39px short of the slide's edge, so nothing
escaped. Content can be made unreadable without ever being cut, and the first two
checks only ever look at one box against its own container. This one looks at two
boxes against each other.

To keep that from reporting a nine-item list against a four-item band thirty-six
times, an overlapping pair is attributed to the two REGIONS it happened inside
(the nearest common ancestor's child on each side) and reported once, carrying
the deepest overlap measured within it. Deliberate layering is excluded: anything
positioned ``absolute``, ``fixed`` or ``sticky``, or inside something that is,
overlaps on purpose.

All three are reported with the slide number, a CSS-ish element label, the
overflow in pixels, and the text involved, so a reviewer can go straight to the
slide.

Deliberately NOT a hard failure by default. A layout defect is not a data-
integrity failure: nothing is lost from the record, and the fix is CSS, the
scaffold, or shorter copy. The human needs to SEE the rendered slide to judge it,
so blocking the write would mean paying for a render and then having nothing to
look at. The deck is written, the report rides along on the result, and the review
UI shows it loudly. ``strict=True`` raises ``LayoutError`` instead, for the eval
scripts and for anywhere a hard gate is wanted.

Degrades to a skip, never a crash: no Chrome on the machine (or a Chrome that
fails to run) returns ``{"ok": True, "checked": False, "skipped": <reason>}`` so a
deck can still be produced on a box without a browser. A skip is reported, not
silent — "we could not check" must never read as "we checked and it was fine".

Nothing here is deck-type or client specific. It measures whatever slides the HTML
carries, so it covers the proposal path, the status path, and any scaffold added
later without being told about it.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile

# Chrome candidates, in order. CHROME_BIN wins so a caller can pin a build; then
# the standard macOS app paths, then anything on PATH (Linux / CI).
_CHROME_ENV_VAR = "CHROME_BIN"
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_CHROME_ON_PATH = ("google-chrome", "google-chrome-stable", "chromium",
                   "chromium-browser", "microsoft-edge")

# Subpixel slack. Rounding, letter-spacing on a final character, and fractional
# grid tracks routinely produce sub-pixel overflow that is invisible on screen;
# flagging it would bury a real 90px truncation in noise.
DEFAULT_TOLERANCE_PX = 2.0

# Overlap needs a wider floor than clipping, and for a structural reason rather
# than a tuning one. Clipping compares an element against its OWN box, where 2px
# of hidden text is genuinely nothing. Overlap compares two boxes that are
# SUPPOSED to sit next to each other: adjacent grid tracks abut by design, and a
# box carries padding, so two boxes can intersect by a few pixels while their
# text does not come close to touching. Measured on the committed decks: this
# floor is the difference between reporting two buried Gantt labels (35px each,
# real) and also reporting a 2.2px track rounding artifact beside them.
OVERLAP_TOLERANCE_PX = 4.0

# A deck slide is a fixed 1280x720 frame; the viewport is wider so the centered
# slide is never itself the thing being squeezed, and taller so a tall stack of
# slides still lays out (each slide has its own fixed height, so this is only
# about giving the document room).
_WINDOW_SIZE = "1500,1200"

# The measuring script. Runs at parse end, writes its findings into a marker
# element as base64 so the serialized DOM carries them out intact regardless of
# what characters the deck's copy contains.
_PROBE_JS = r"""
(function () {
  var TOL = __TOL__;
  var OVERLAP_TOL = __OVERLAP_TOL__;
  var findings = [];
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));

  function slideNumberOf(el) {
    var s = el.closest ? el.closest('.slide') : null;
    return s ? slides.indexOf(s) + 1 : 0;
  }
  function clipsOn(el) {
    var cs = getComputedStyle(el);
    return { x: cs.overflowX !== 'visible', y: cs.overflowY !== 'visible' };
  }
  function ownText(el) {
    var t = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3) t += n.nodeValue;
    }
    return t.replace(/\s+/g, ' ').trim();
  }
  function label(el) {
    var name = el.tagName.toLowerCase();
    var cls = (typeof el.className === 'string') ? el.className.trim() : '';
    if (cls) name += '.' + cls.split(/\s+/).join('.');
    return name;
  }
  function clip(text, n) {
    return text.length > n ? text.slice(0, n) + '…' : text;
  }
  // The nearest ancestor (excluding el) that clips on either axis, plus its
  // padding box — the box its content is actually cut to.
  function clippingAncestor(el) {
    var a = el.parentElement;
    while (a) {
      var c = clipsOn(a);
      if (c.x || c.y) return { el: a, clips: c };
      a = a.parentElement;
    }
    return null;
  }
  function paddingBox(el) {
    var r = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    var left = r.left + parseFloat(cs.borderLeftWidth || 0);
    var top = r.top + parseFloat(cs.borderTopWidth || 0);
    return { left: left, top: top,
             right: left + el.clientWidth, bottom: top + el.clientHeight };
  }

  // Deliberate layering is done with positioning, so a positioned element (or
  // anything inside one) is excluded from the overlap check below rather than
  // reported as an accident. Stops at the slide, which is itself positioned.
  function layered(el) {
    var a = el;
    while (a && !(a.classList && a.classList.contains('slide'))) {
      var p = getComputedStyle(a).position;
      if (p === 'absolute' || p === 'fixed' || p === 'sticky') return true;
      a = a.parentElement;
    }
    return false;
  }
  // The two REGIONS that overlap, rather than the two words that happened to
  // touch. Walks to the nearest common ancestor and takes its child on each
  // side, so a nine-item list spilling onto a four-item band reports once as
  // its container against that band, not thirty-six times.
  function regionPair(a, b) {
    var common = a;
    while (common && !common.contains(b)) common = common.parentElement;
    if (!common) return null;
    var ra = a;
    while (ra.parentElement && ra.parentElement !== common) ra = ra.parentElement;
    var rb = b;
    while (rb.parentElement && rb.parentElement !== common) rb = rb.parentElement;
    return [ra, rb];
  }

  var painted = [];
  var nodes = document.querySelectorAll('.slide, .slide *');
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    var rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;   // not rendered
    var text = ownText(el);
    var self = clipsOn(el);

    if (text && !layered(el)) {
      painted.push({ el: el, rect: rect, text: text, slide: slideNumberOf(el) });
    }

    // 1. CLIPPED — the element's own content overflows a box that clips it.
    if (text) {
      var overX = el.scrollWidth - el.clientWidth;
      var overY = el.scrollHeight - el.clientHeight;
      if (self.x && overX > TOL) {
        findings.push({ kind: 'clipped', axis: 'horizontal', slide: slideNumberOf(el),
                        element: label(el), overflow_px: Math.round(overX * 10) / 10,
                        text: clip(text, 120) });
      } else if (self.y && overY > TOL) {
        findings.push({ kind: 'clipped', axis: 'vertical', slide: slideNumberOf(el),
                        element: label(el), overflow_px: Math.round(overY * 10) / 10,
                        text: clip(text, 120) });
      }
    }

    // 2. ESCAPED — the element's box runs past the container that clips it.
    var anc = clippingAncestor(el);
    if (anc) {
      var box = paddingBox(anc.el);
      var over = 0, axis = '', side = '';
      if (anc.clips.x) {
        if (rect.right - box.right > over + TOL) { over = rect.right - box.right; axis = 'horizontal'; side = 'right'; }
        if (box.left - rect.left > over + TOL) { over = box.left - rect.left; axis = 'horizontal'; side = 'left'; }
      }
      if (anc.clips.y) {
        if (rect.bottom - box.bottom > over + TOL) { over = rect.bottom - box.bottom; axis = 'vertical'; side = 'bottom'; }
        if (box.top - rect.top > over + TOL) { over = box.top - rect.top; axis = 'vertical'; side = 'top'; }
      }
      if (over > TOL) {
        findings.push({ kind: 'escaped', axis: axis, side: side, slide: slideNumberOf(el),
                        element: label(el), container: label(anc.el),
                        overflow_px: Math.round(over * 10) / 10,
                        text: clip(text || ownText(anc.el), 120) });
      }
    }
  }

  // 3. OVERLAPPED — two elements that each paint their OWN text, on one slide,
  //    whose boxes intersect while neither contains the other. This is the case
  //    checks 1 and 2 are both structurally blind to: when every box in the
  //    chain has `overflow:visible`, a list too long for its panel is neither
  //    clipped (nothing clips it) nor escaped (it never leaves the slide) — it
  //    is simply painted on top of whatever sits below, and both boxes still
  //    report their own geometry as clean.
  var regions = {};
  for (var a = 0; a < painted.length; a++) {
    for (var b = a + 1; b < painted.length; b++) {
      var A = painted[a], B = painted[b];
      if (A.slide !== B.slide) continue;
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      var iw = Math.min(A.rect.right, B.rect.right) - Math.max(A.rect.left, B.rect.left);
      var ih = Math.min(A.rect.bottom, B.rect.bottom) - Math.max(A.rect.top, B.rect.top);
      if (iw <= OVERLAP_TOL || ih <= OVERLAP_TOL) continue;
      var pair = regionPair(A.el, B.el);
      if (!pair) continue;
      var key = A.slide + '|' + label(pair[0]) + '|' + label(pair[1]);
      // The EXTENT of the damage across the whole region, not the depth of one
      // pair. A list spilling onto a band overlaps it one line at a time, so the
      // worst single pair is one line tall (~35px) while the band is buried
      // under 174px of text. Every rectangle unioned here is real overlap, so
      // its bounding box is the honest answer to "how much is covered".
      var seen = regions[key];
      if (!seen) {
        seen = regions[key] = { kind: 'overlapped', slide: A.slide,
                                element: label(pair[0]), other: label(pair[1]),
                                left: Infinity, top: Infinity,
                                right: -Infinity, bottom: -Infinity,
                                deepest: 0, text: '', other_text: '' };
      }
      seen.left = Math.min(seen.left, Math.max(A.rect.left, B.rect.left));
      seen.top = Math.min(seen.top, Math.max(A.rect.top, B.rect.top));
      seen.right = Math.max(seen.right, Math.min(A.rect.right, B.rect.right));
      seen.bottom = Math.max(seen.bottom, Math.min(A.rect.bottom, B.rect.bottom));
      // The sample text quotes the worst-covered pair, so a reviewer reads the
      // words that are actually unreadable rather than whichever pair came first.
      if (Math.min(iw, ih) >= seen.deepest) {
        seen.deepest = Math.min(iw, ih);
        seen.text = clip(A.text, 120);
        seen.other_text = clip(B.text, 120);
      }
    }
  }
  for (var key in regions) {
    if (!Object.prototype.hasOwnProperty.call(regions, key)) continue;
    var r = regions[key];
    findings.push({ kind: 'overlapped', slide: r.slide,
                    element: r.element, other: r.other,
                    overflow_px: Math.round(
                      Math.min(r.right - r.left, r.bottom - r.top) * 10) / 10,
                    text: r.text, other_text: r.other_text });
  }

  var report = { slides: slides.length, findings: findings };
  var json = JSON.stringify(report);
  var bytes = new TextEncoder().encode(json);
  var binary = '';
  for (var b = 0; b < bytes.length; b++) binary += String.fromCharCode(bytes[b]);
  var marker = document.createElement('div');
  marker.id = '__layout_report__';
  marker.setAttribute('data-report', btoa(binary));
  document.body.appendChild(marker);
})();
"""

_MARKER_RE = re.compile(
    r'id="__layout_report__"[^>]*data-report="([^"]*)"'
    r'|data-report="([^"]*)"[^>]*id="__layout_report__"'
)


class LayoutError(AssertionError):
    """A rendered deck clips or overflows content.

    Subclasses ``AssertionError`` so a strict run fails as loudly as a broken
    invariant, matching ``CoverageError``, ``RenderFidelityError``, and
    ``ConsistencyError``. Raised only when the caller passes ``strict=True``.
    """


def find_chrome():
    """Path to a usable Chrome/Chromium/Edge binary, or ``None``.

    ``CHROME_BIN`` overrides everything, so a caller can pin a specific build
    without editing this list.
    """
    pinned = os.environ.get(_CHROME_ENV_VAR)
    if pinned and os.path.isfile(pinned):
        return pinned
    for candidate in _CHROME_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    for name in _CHROME_ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    return None


def _instrument(html, tolerance, overlap_tolerance):
    """Return ``html`` with the measuring script appended inside the body."""
    script = (_PROBE_JS
              .replace('__TOL__', repr(float(tolerance)))
              .replace('__OVERLAP_TOL__', repr(float(overlap_tolerance))))
    probe = f"\n<script>{script}</script>\n"
    lowered = html.lower()
    index = lowered.rfind("</body>")
    if index == -1:
        return html + probe
    return html[:index] + probe + html[index:]


def _run_probe(chrome, html, tolerance, overlap_tolerance, timeout):
    """Render ``html`` headless and return the parsed report dict.

    Raises ``RuntimeError`` when Chrome cannot be driven or its output carries no
    report — the caller turns that into a reported skip, never a crash.
    """
    workdir = tempfile.mkdtemp(prefix="layout-guard-")
    try:
        page = os.path.join(workdir, "deck.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(_instrument(html, tolerance, overlap_tolerance))
        # No --user-data-dir. A fresh throwaway profile would be the tidier
        # isolation, but on macOS Chrome 141 it hangs indefinitely on first-run
        # profile setup (measured 2026-07-24: every other flag combination returns
        # in under a second, adding --user-data-dir never returns). --headless=new
        # already runs its own browser process rather than attaching to a running
        # Chrome, and the deck is a self-contained local file with no network
        # requests, so the default profile cannot change what gets measured.
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-sandbox",
            "--no-first-run",
            "--force-device-scale-factor=1",
            f"--window-size={_WINDOW_SIZE}",
            "--virtual-time-budget=3000",
            "--dump-dom",
            f"file://{page}",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"could not run {os.path.basename(chrome)}: {exc}")
        match = _MARKER_RE.search(completed.stdout or "")
        if not match:
            detail = (completed.stderr or "").strip().splitlines()
            tail = detail[-1] if detail else "no report marker in the dumped DOM"
            raise RuntimeError(f"headless render produced no measurements ({tail})")
        payload = match.group(1) or match.group(2)
        try:
            return json.loads(base64.b64decode(payload).decode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"unreadable measurement payload: {exc}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def describe_finding(finding):
    """One human-readable line for a finding, for a log or the review UI."""
    where = f"Slide {finding['slide']}" if finding.get("slide") else "Deck"
    element = finding.get("element", "?")
    pixels = finding.get("overflow_px", 0)
    text = finding.get("text") or ""
    if finding.get("kind") == "clipped":
        return (f"{where}: {element} clips its own text by {pixels}px "
                f"({finding.get('axis', '')}) — \"{text}\"")
    if finding.get("kind") == "overlapped":
        return (f"{where}: {element} paints {pixels}px over "
                f"{finding.get('other', '?')}, so both sets of words sit on top "
                f"of each other — \"{text}\" over "
                f"\"{finding.get('other_text', '')}\"")
    return (f"{where}: {element} runs {pixels}px past the {finding.get('side', '')} "
            f"edge of {finding.get('container', '?')}, so its content is cut "
            f"— \"{text}\"")


def check_layout(rendered_html, *, strict=False, tolerance=DEFAULT_TOLERANCE_PX,
                 overlap_tolerance=OVERLAP_TOLERANCE_PX, chrome=None,
                 timeout=30):
    """Measure a rendered deck and report clipped, overlapped or escaped content.

    Returns a report dict:

    ``{"ok": bool, "checked": bool, "skipped": str, "slides": int,
       "findings": [{...}], "summary": [str, ...]}``

    ``ok`` is False only when a real finding was measured. A skip (no Chrome, or a
    headless run that failed) reports ``checked=False`` with a ``skipped`` reason
    and ``ok=True``, so a machine without a browser can still produce decks — but
    the reason is always in the report, because "could not check" must never be
    mistaken for "checked and clean".

    With ``strict=True`` a finding raises ``LayoutError`` listing every one. A skip
    still does not raise: an unavailable browser is an environment fact, not a
    defect in the deck.
    """
    report = {"ok": True, "checked": False, "skipped": "", "slides": 0,
              "findings": [], "summary": []}

    binary = chrome or find_chrome()
    if not binary:
        report["skipped"] = (
            "no Chrome/Chromium/Edge found — install one or set CHROME_BIN to "
            "measure deck layout"
        )
        return report

    try:
        measured = _run_probe(binary, rendered_html, tolerance,
                              overlap_tolerance, timeout)
    except RuntimeError as exc:
        report["skipped"] = str(exc)
        return report

    findings = measured.get("findings") or []
    # Worst overflow first: a 90px truncation matters more than a 3px one.
    findings.sort(key=lambda f: -float(f.get("overflow_px") or 0))
    report.update({
        "checked": True,
        "slides": measured.get("slides", 0),
        "findings": findings,
        "summary": [describe_finding(f) for f in findings],
        "ok": not findings,
    })

    if strict and findings:
        listing = "\n".join(f"  - {line}" for line in report["summary"])
        raise LayoutError(
            "the rendered deck clips, overlaps, or pushes content out of view, "
            "so a slide reads as less than the deck carries:\n"
            f"{listing}\n"
            "Fix the scaffold's geometry or shorten the copy. The HTML still "
            "contains the full text, which is why no string-level guard catches "
            "this."
        )
    return report
