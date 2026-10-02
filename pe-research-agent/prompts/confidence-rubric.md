# confidence-rubric.md
# QofAI Research Agent — Confidence Band Rubric
*v1 — Module 3 pre-work, Summer 2026*

---

## Changelog

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| v1 | [YYYY-MM-DD] | [intern name] | Initial rubric, examples drawn from Example Capital v1 dossier |

To update this document: increment the version number, add a row to this table, and note what
changed and why. Do not edit prior rows. Add new examples as better or more instructive cases
emerge from subsequent dossier runs.

---

## Purpose

This document provides worked examples of high, medium, and low confidence labels drawn from the
Example Capital v1 manual dossier. It exists to calibrate the confidence-scorer skill: when the
skill encounters a claim and must assign a band, it should check its assignment against the
patterns established here.

This rubric is a companion to research-agent.md, which contains the authoritative definitions of
each confidence band. When this document and research-agent.md conflict, research-agent.md takes
precedence. When an example here seems to contradict a general rule there, flag the conflict in
the run log and surface it to a human reviewer rather than resolving it silently.

The examples below are organized by band. Within each band, examples are ordered from
straightforward to edge case. The edge cases are the ones most likely to trip up the
confidence-scorer skill, and they receive the most explanation.

---

## How to Use This Rubric

For each claim the confidence-scorer skill evaluates, it should:

1. Identify the source type of the claim (public document, public inference, or private
   inference — see source-rubric.md for definitions).
2. Ask whether the source is self-reported by the subject firm or portfolio company.
3. Ask whether the claim is directly stated or inferred from multiple signals.
4. Ask whether the claim could be independently verified by a third party without access to
   non-public information.
5. Match the claim to the closest example in this rubric and assign the same band, adjusting
   up or down only if the claim's source or inferential basis is materially stronger or weaker
   than the matched example. A materially stronger source is one that is independently audited,
   filed under regulatory penalty, or corroborated by two or more non-subject primary sources.
   A materially weaker source is one that is user-edited, unattributed, or relies on a single
   aggregator with no traceable primary source.

When no example matches closely enough to anchor the assignment, default to the more
conservative band and note the reason in the run log.

---

## Confidence Decision Tree

Apply these checks in order. Stop at the first match.

1. Is the claim unsourced entirely? Do not label it. Mark it unavailable and flag it as a
   critical audit-pass issue.
2. Is the claim based only on a user-edited or aggregator source (Crunchbase, Wikipedia,
   PitchBook user-submitted content)? Low.
3. Is the claim self-reported by the subject firm or portfolio company (ADV financials, firm
   website, firm press release, company website)? Medium at best.
4. Is the claim an editorial comparative ("notably high," "unusually long") without a cited
   benchmark? Remove it. Do not label it.
5. Is the claim an inference drawn from multiple public documents rather than directly stated
   in any one source? Medium, unless the inference requires large extrapolation or assumptions
   not grounded in the documents, in which case low.
6. Is the claim independently reported, filed under regulatory consequence, or corroborated by
   two or more non-subject primary sources? High, unless the source is stale beyond the
   thresholds in research-agent.md or contradicted by another source.

---

## Common PE Claim Types — Default Bands

Use this table as a first-pass default before consulting the worked examples. The upgrade and
downgrade conditions override the default when they apply.

| Claim type | Typical source | Default band | Upgrade condition | Downgrade condition |
|---|---|---|---|---|
| Regulatory AUM | ADV filing | Medium | None — ADV figures are always self-reported | Filing older than 90 days; add staleness flag |
| Headquarters address | ADV + firm website | High | Corroborated across two or more current independent sources | Only one source, or sources conflict |
| Satellite office address | ADV only | Medium | Corroborated by firm website or independent source | Not corroborated; may be registered address only |
| Full-time headcount | ADV / firm website | Medium | Independent current news source with named figure | Source older than 90 days; add staleness flag |
| Capital raised / fund size | Firm press release | Medium | Independently reported by a non-subject financial publication | No corroboration; estimate only |
| Partner name and current role | Firm website / press release | Medium | Independent news coverage naming the role | Source older than 12 months; add staleness flag |
| Partner tenure dates | Firm press release | Medium | None — tenure dates are self-reported | Sole source is Crunchbase or LinkedIn only |
| Portfolio company revenue | Company website / inference | Low | Audited filing or credible third-party report | Estimate from headcount or comps only |
| Aggregate portfolio revenue | Firm press release | Medium | None — aggregate figures are always self-reported and promotional | No corroboration at all |
| Active portfolio status | Firm website | Medium | Recent independently reported deal announcement | No confirmation in past 12 months; add staleness flag |
| Platform founding date | Press release | Medium | Corroborated by independent source | Date refers to platform vehicle, not organic founding — flag distinction |
| Deal close date | Press release | Medium | Corroborated by independent deal coverage | Date unverified against stated window |
| Exit date | Press release / PitchBook | Medium | Corroborated by independent coverage | PitchBook only with no press corroboration |

---

## High Confidence

### Definition summary

The claim comes from a primary source that is independently verifiable and not self-reported by
the subject firm or the portfolio company being described. A third party could confirm or refute
the claim without access to non-public information.

### Examples from the Example Capital v1 dossier

---

**Example H-1: Founding year**

Claim: "Example Capital is the rebranded successor to Example Partners, which was
founded in 1998."

Band: high

Reasoning: The founding year of Example Partners is corroborated by multiple
independent sources including historical SEC filings, independently reported news coverage, and
the firm's own ADV filing. The founding year of a firm that has been continuously registered
with the SEC since 1995 is not a self-reported claim that could be inflated or misrepresented
without regulatory consequence. It is checkable against the public record and independently
verifiable.

Correct label: *(confidence: high | source: public document — historical SEC filings and
independently reported news coverage, corroborated by ADV filing, all accessed June 2026)*

---

**Example H-2: Rebrand date**

Claim: "Example Capital launched its current identity in October 2025."

Band: high (with a caveat — see note)

Reasoning: The rebrand date is sourced to a press release and corroborated by the SEC ADV
amendment date, which is a filed public document. The specific month is verifiable against the
amendment filing date.

Note: The v1 dossier assigned this as high confidence without verifying the ADV amendment date
against the press release date. If those dates conflict, the band drops to medium until the
discrepancy is resolved. The confidence-scorer skill should flag this as high only if the ADV
amendment date and press release date have been confirmed to agree.

Correct label: *(confidence: high | source: public document — press release and ADV amendment,
both accessed June 2026)*

---

**Example H-3: Headquarters address**

Claim: "The firm is headquartered at 100 Example Avenue, 12th Floor, New York, NY."

Band: high

Reasoning: The address is listed in the SEC ADV filing. Office addresses in ADV filings are
subject to regulatory accuracy requirements and are corroborated by the firm website. An address
that appears in both an ADV filing and the firm's own public-facing materials, and that has not
changed across recent filings, is high confidence.

Note: Example City office listed in the same ADV is a different case. A single satellite office
listed only in an ADV without corroboration from the firm website or any independent source
should be labeled medium confidence with a note that ADV-listed satellite addresses are sometimes
registered addresses rather than operating offices. See Example M-3 below.

Correct label: *(confidence: high | source: public document — SEC ADV filing and firm website,
both accessed June 2026)*

---

**Example H-4: Fund count and capital raised**

Claim: "The firm has raised over $4.5 billion in initial capital commitments through eight private
equity funds since 1995."

Band: medium, not high (this is a correction of the v1 dossier)

Reasoning: This claim is sourced to a firm press release and a Example Bank deal card. Both are
self-reported or promotional materials produced by or on behalf of the firm. Capital commitment
figures for private funds are not publicly audited. The v1 dossier labeled this high confidence,
which is an error. Self-reported capital figures from press releases are medium confidence
regardless of how specific they appear.

This example is included in the high confidence section as a negative case: a claim that looks
like it should be high confidence but is not, because the source is self-reported.

Correct label: *(confidence: medium | source: public document — firm press release, self-reported)*

---

## Medium Confidence

### Definition summary

The claim comes from a self-reported source (firm website, firm press release, portfolio company
website, ADV financial figures) or is inferred from multiple public signals but not directly
stated by a primary non-subject source. The claim is plausible and sourced, but a reader cannot
independently verify it without access to non-public information.

### Examples from the Example Capital v1 dossier

---

**Example M-1: Regulatory AUM**

Claim: "Example Capital manages approximately $3.1 billion in regulatory assets under management."

Band: medium

Reasoning: Regulatory AUM figures come from the SEC ADV filing. ADV filings are public documents
but are self-reported by the firm and can lag reality by up to 90 days from the annual amendment
deadline. Regulatory AUM is also a distinct figure from committed capital or invested capital,
which are the figures a PE audience would typically want and which are not publicly disclosed for
most private funds. A reader who sees $3.1 billion and assumes it means committed capital is
drawing a false inference.

Required disclosure: when reporting this figure, the dossier must note that it reflects
regulatory AUM as defined by the SEC, not committed or invested capital, and that the figure may
lag by up to 90 days.

Correct label: *(confidence: medium | source: public document — SEC ADV filing, self-reported,
lag up to 90 days; note: regulatory AUM is distinct from committed or invested capital)*

---

**Example M-2: Headcount**

Claim: "Example Capital operates with a full-time staff of 23."

Band: medium

Reasoning: Headcount from an ADV filing is self-reported. The firm controls what it reports, and
the figure may not reflect recent hires, departures, or contractors. A 23-person headcount figure
from an ADV is a reasonable approximation but not a verifiable fact. The v1 dossier labeled this
high confidence, which is an error of the same type as Example H-4.

Correct label: *(confidence: medium | source: public document — SEC ADV filing, self-reported,
lag up to 90 days)*

---

**Example M-3: Satellite office**

Claim: "The firm has a second office in Example City, Florida."

Band: medium

Reasoning: Example City office is listed in the ADV filing but does not appear on the firm
website or in any independently reported source reviewed. ADV-listed addresses are sometimes
registered addresses or mail drops rather than staffed operating offices. Without corroboration
from a second source, the existence of an operating office in Example City is plausible but
unverified.

Correct label: *(confidence: medium | source: public document — SEC ADV filing; note: ADV-listed
satellite addresses are sometimes registered addresses rather than operating offices, unconfirmed
by second source)*

---

**Example M-4: Named partner roles sourced to firm press releases**

Claim: "Morgan Partner is Managing Partner; he joined in 2007 and was named Managing Partner
in 2020."

Band: medium

Reasoning: Partner roles and tenure dates sourced to firm press releases are self-reported. The
firm controls the content of its own press releases and has an interest in presenting its team
favorably. Tenure dates that appear only in firm-issued materials cannot be independently
verified. This is medium confidence, not high.

Note: the v1 dossier labeled this high confidence and cited "firm press releases, ZoomInfo,
Crunchbase" as a single bundled source. ZoomInfo aggregates from public sources but is not
independent verification. Crunchbase is user-edited. Neither upgrades a press release claim to
high confidence. Bundling weak sources together does not strengthen the confidence band.

Correct label: *(confidence: medium | source: public document — firm press release, self-reported)*

---

**Example M-5: Portfolio company operational metrics from company website**

Claim: "Example Imaging has 110 centers across 30 states and over 300 affiliated
radiologists."

Band: medium

Reasoning: These figures come from the Example Imaging website. A company reporting its own operational
scale on its own website is making a marketing claim, not filing an audited statement. The
figures may be accurate, but they are not independently verified. The v1 dossier labeled this
high confidence, which is an error. Company-reported operational metrics on a company website
are always medium confidence at best.

Correct label: *(confidence: medium | source: public document — Example Imaging website, company-reported,
not independently verified, accessed June 2026)*

---

**Example M-6: Aggregate portfolio metrics from firm press release**

Claim: "Portfolio companies today generate $3 billion in aggregate revenue and employ
approximately 60,000 people."

Band: medium

Reasoning: Aggregate portfolio metrics sourced to a firm press release and a Example Bank deal card are
self-reported and promotional. The firm has a clear incentive to present large aggregate figures
to LPs and the market. For a firm with 21 active holdings of varying size, independently
verifying aggregate revenue from public sources is not feasible. The v1 dossier labeled this high
confidence, which is the most significant miscalibration in the document. These figures could be
materially wrong and a reader would have no way to check.

Correct label: *(confidence: medium | source: public document — firm press release and Example Bank deal
card, self-reported and promotional)*

---

**Example M-7: Inferred sector strategy**

Claim: "The firm's approach centers on complex, fragmented industries where sector specialization
surfaces opportunities that generalist buyers overlook."

Band: medium

Reasoning: This framing comes from the firm website and press materials. It reflects the firm's
own characterization of its strategy, not an independently verified observation. It is plausible
and consistent with the observable portfolio, but it is the firm's own account. Labeling it high
confidence would imply independent verification that does not exist.

Correct label: *(confidence: medium | source: public document — firm website, self-reported
strategic framing)*

---

**Example M-8: Inferred deal pattern from portfolio observation**

Claim: "The clustering of recent deal activity across fuel distribution, packaging, and
healthcare services suggests the firm is deepening sector concentration rather than broadening."

Band: medium

Reasoning: This is a conclusion drawn from observing multiple publicly disclosed investments. The
individual deal disclosures are public documents. The inference about strategic direction is drawn
from the pattern across those documents rather than from any single stated source. This is public
inference, which sits at medium confidence. The reasoning chain is transparent and the conclusion
is well-supported, but it is still an inference and must be labeled as such.

Correct label: *(confidence: medium | source: public inference — based on review of disclosed
platform investments as of June 2026)*

---

## Low Confidence

### Definition summary

The claim is inferred from weak or indirect signals, involves significant extrapolation, comes
from a source with known reliability problems, or cannot be verified against any public document.
Low confidence claims are acceptable in a dossier when they are clearly labeled. Unlabeled low
confidence claims are a critical audit-pass flag.

### Examples from the Example Capital v1 dossier

---

**Example L-1: Revenue range inferred from portfolio**

Claim: "Target businesses appear to operate in the $25 million to $500 million revenue range."

Band: low

Reasoning: The firm does not publicly disclose its target revenue range. This figure is inferred
from portfolio company profiles and general knowledge of middle-market PE conventions. The range
is extremely wide, spanning three distinct market segments (lower middle market, core middle
market, upper middle market). It tells a reader almost nothing precise about deal targeting. The
v1 dossier labeled this low confidence and private inference, which is correct. It is included
here as a positive example of appropriate labeling.

Additional note: even correctly labeled low confidence claims should be accompanied by a note on
the imprecision where the imprecision is itself material. A $475 million range is not a useful
targeting signal, and the dossier should say so explicitly rather than implying the inference is
useful simply because it is labeled.

Correct label: *(confidence: low | source: private inference — no public disclosure of target
revenue range; note: range spans lower, core, and upper middle market and carries limited
precision as a targeting signal)*

---

**Example L-2: Co-founder current role sourced solely to Crunchbase**

Claim: "Sam Partner is listed as Co-Founder and Executive Chairman on Crunchbase, suggesting a
founder who remains affiliated but is no longer in a day-to-day investment role."

Band: low

Reasoning: Crunchbase is a user-edited database. Any individual or anyone with access to the
platform can edit a Crunchbase entry. Using it as the sole source for a claim about a named
individual's current title and relationship to a firm is weak sourcing. The v1 dossier labeled
this medium confidence, which understates the weakness. A Crunchbase-only claim about a current
role is low confidence until corroborated by a primary source.

This is also an incomplete treatment of a material gap. Partner's absence from all current firm
materials — website, press releases, ADV — is unexplained. The dossier should flag the gap
explicitly rather than offering a Crunchbase inference as a partial explanation. See the
Unexplained Organizational History Gaps rule in research-agent.md.

Correct label: *(confidence: low | source: public document — Crunchbase entry, user-edited,
not corroborated by any primary source; note: Partner does not appear in current firm website,
press releases, or ADV; current role and relationship to firm are unverified)*

---

**Example L-3: Departure of Robin Partner — absence treated as non-issue**

Claim: The v1 dossier names Robin Partner as Co-President in 2020 and does not address his
absence from current leadership materials.

Band: this is not a labeling error but a refusal behavior violation — an unexplained
organizational history gap.

Reasoning: Partner was named Co-President alongside Taylor Partner in 2020 per a firm press
release. He does not appear anywhere in current firm materials. His departure is unacknowledged
in the v1 dossier. This is not a confidence calibration issue; it is a gap that the dossier
should surface explicitly regardless of confidence band. The correct behavior is to name the gap,
name the last known source, and state that his current status is unverified.

What the dossier should have said: "Robin Partner was named Co-President in 2020 per a firm press
release from that period. He does not appear in current leadership materials, the firm website,
or any post-2021 source reviewed for this dossier. His current role or departure from the firm
is unverified. (confidence: low | source: public document — 2020 press release; current status:
unknown)"

---

**Example L-4: Deal pace comparative claim**

Claim: "Example Capital closed five platform investments in the 18 months leading up to the rebrand — a
notably high pace for a 19-person firm managing $3.1 billion."

Band: the "notably high pace" descriptor is low confidence and should not appear at all without
a benchmark; the deal count and window are medium confidence pending date verification.

Reasoning: "Notably high pace" is an editorial judgment presented as a contextual fact. No
benchmark is cited for what constitutes a normal or high deployment pace for a firm of comparable
AUM and headcount. Middle-market PE firms of similar size vary widely in deployment cadence
depending on strategy, fund vintage, and market conditions. Without a cited comparison point,
the characterization is speculation dressed as observation.

The deal count itself (five investments in 18 months) is a checkable claim. The v1 dossier does
not show the date verification math. Until each deal's close date is confirmed against the stated
window, the count is medium confidence, not high.

Correct treatment: report the count and the window, show the date math, and omit the comparative
descriptor unless a benchmark is available.

Correct label for the factual portion: *(confidence: medium | source: public document — press
releases; note: close dates verified against the April 2024 to October 2025 window — [list
dates here])*

---

**Example L-5: Strategic interpretation and AI deployment speculation**

Claim: "The firm's concentration in labor-intensive, recurring-revenue businesses makes it a
natural candidate for operational AI deployment."

Band: low — speculation

Reasoning: This claim draws on private inference about QofAI's own assessment of AI deployment
fit. It is not sourced to any public document or observable fact about Example Capital specifically. It
is a business development judgment made by the analyst, not a finding from research. The v1
dossier labeled this correctly as low confidence and speculation. It is included here as a
positive example of appropriate labeling on a highly inferential claim.

Note: this type of claim is the most self-serving in the dossier given QofAI's business purpose,
and it should be held to the highest standard of labeling discipline. Correct labeling does not
make the claim acceptable to include without caveat; it makes the nature of the claim transparent
to a skeptical reader.

Correct label: *(confidence: low | source: private inference — speculation; note: reflects
analyst judgment about AI deployment fit, not a finding from public research)*

---

## Calibration Errors Summary

The following table summarizes every confidence miscalibration identified in the Example Capital
v1 dossier. The confidence-scorer skill should treat these as negative training examples and
actively check for the same patterns in future dossier output.

| Claim | v1 Label | Correct Label | Error Type |
|-------|----------|---------------|------------|
| $4.5B+ capital raised, 8 funds | high | medium | Self-reported source mislabeled high |
| $3.1 B regulatory AUM | high | medium | Self-reported ADV figure mislabeled high |
| 23 full-time staff | high | medium | Self-reported ADV figure mislabeled high |
| $3B aggregate portfolio revenue | high | medium | Promotional source mislabeled high |
| 45,000 employees across portfolio | high | medium | Promotional source mislabeled high |
| Example Imaging 110 centers, 300+ radiologists | high | medium | Company self-reported metric mislabeled high |
| Partner roles and tenure dates | high | medium | Self-reported press release mislabeled high |
| Sam Partner current role | medium | low | User-edited source overcalibrated |
| "Notably high pace" descriptor | high | low | Editorial judgment without benchmark labeled as sourced fact |
| "Unusually long average tenure" | unstated | low | Editorial judgment without benchmark presented without label |
| Five deals in 18 months | high | medium | Checkable claim labeled high without showing verification |

---

## Rules Derived from These Examples

These rules are distilled from the calibration errors above. The confidence-scorer skill applies
them in order when evaluating any claim.

1. Self-reported sources are medium at best. A firm reporting its own AUM, headcount, capital
   raised, or portfolio metrics is the sole author of that claim. Regardless of whether the
   source is a public document (ADV, press release, website), self-reported figures do not
   qualify for high confidence.

2. Aggregating weak sources does not strengthen a claim. Citing "press releases, ZoomInfo,
   Crunchbase" as a bundled source does not produce a high-confidence claim if each individual
   source is medium or low. The band is set by the weakest source that the claim depends on.

3. Company-reported operational metrics are marketing claims. A company's own website reporting
   its own center count, headcount, or revenue is a marketing claim until independently audited
   or corroborated. Label it medium.

4. Editorial comparatives require a benchmark or they do not appear. "Notably high,"
   "unusually long," "impressively fast" are low confidence claims masquerading as contextual
   observations. If no benchmark is cited, the descriptor is removed, not labeled.

5. Crunchbase-only role claims are low confidence. User-edited databases are acceptable as
   corroboration alongside a primary source. They are not acceptable as the sole basis for a
   claim about a named individual's current title or relationship to a firm.

6. Absence is a finding. When a named person does not appear in current sources, that absence
   is a low-confidence finding that must be labeled and surfaced, not silently omitted.
