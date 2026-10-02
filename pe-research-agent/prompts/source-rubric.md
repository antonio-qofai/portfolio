# source-rubric.md
# QofAI Research Agent — Source Type Rubric
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

This document provides worked examples distinguishing public document, public inference, and
private inference, drawn from the Example Capital v1 manual dossier. It exists to calibrate the
source-typer skill: when the skill encounters a claim and must assign a source type, it should
check its assignment against the patterns established here.

This rubric is a companion to research-agent.md, which contains the authoritative definitions of
each source type, and to confidence-rubric.md, which covers confidence band calibration. Source
type and confidence band are related but distinct. A claim can be a public document with medium
confidence (self-reported), a public inference with medium confidence (well-reasoned from strong
sources), or a private inference with low confidence (thin extrapolation). When this document and
research-agent.md conflict, research-agent.md takes precedence. Flag conflicts in the run log
rather than resolving them silently.

The examples below are organized by source type. Within each type, examples run from
straightforward to edge case. Edge cases receive the most explanation because they are where the
source-typer skill is most likely to miscategorize.

---

## How to Use This Rubric

For each claim the source-typer skill evaluates, it should:

1. Ask whether the claim is directly stated in a named, retrievable document. If yes, it is a
   public document. Go to step 2 to check for self-reporting, which affects confidence but not
   source type.
2. Ask whether the named document was published by the subject firm, a portfolio company, or
   anyone with a direct interest in how the claim is perceived. If yes, it is still a public
   document, but the confidence band is medium at best regardless of how specific the claim
   appears.
3. If the claim is not directly stated in any single document, ask whether it is a conclusion
   drawn from two or more public documents by connecting evidence the documents do not connect
   themselves. If yes, it is a public inference. The reasoning chain must be shown in the dossier.
4. If the claim requires assumptions, estimates, or knowledge that no public document supports,
   it is a private inference. Label speculation explicitly when the basis is especially thin.

When in doubt between public inference and private inference, ask: could a reader reconstruct
the reasoning using only public documents? If yes, public inference. If the reasoning requires
non-public knowledge, industry experience, or assumptions the reader cannot verify, private
inference.

---

## Source Type Decision Tree

Apply these checks in order. Stop at the first match.

1. Is the claim directly stated in a named, retrievable document that exists in the public
   record? Public document.
2. Is the claim a conclusion drawn by connecting evidence across two or more public documents,
   where no single document states the conclusion directly? Public inference. Show the reasoning
   chain.
3. Does the claim require assumptions, estimates, or knowledge that no public document supports,
   including industry benchmarks, financial extrapolation, or analyst judgment? Private inference.
3a. Does the claim contain both a public inference component and a private inference component
    in the same sentence or passage? Label the whole claim at the weaker type (private inference)
    and note the distinction inline: identify which part is grounded in public documents and which
    part requires non-public judgment. Do not split the claim artificially if doing so would
    misrepresent the argument being made.
4. Is the basis especially thin or does the claim reflect the analyst's own judgment rather than
   any sourced evidence? Private inference — label as speculation.

---

## Common PE Claim Types — Default Source Types

Use this table as a first-pass default before consulting the worked examples.

| Claim type | Default source type | Notes |
|---|---|---|
| Regulatory AUM | Public document | ADV filing; self-reported, affects confidence not source type |
| Headquarters address | Public document | ADV filing and firm website |
| Headcount | Public document | ADV filing; self-reported |
| Fund count and capital raised | Public document | Firm press release; self-reported and promotional |
| Partner name and current role | Public document | Firm website or press release; self-reported |
| Partner tenure dates | Public document | Firm press release; self-reported |
| Sector focus (stated) | Public document | Firm website; self-reported |
| Sector focus (inferred from portfolio) | Public inference | Conclusion drawn from observing multiple disclosed investments |
| Deal size range (stated) | Public document | Firm website or press release; self-reported |
| Deal size range (inferred) | Public inference | Drawn from portfolio company profiles and deal announcements |
| Portfolio company operational metrics | Public document | Company website; company-reported, affects confidence |
| Aggregate portfolio metrics | Public document | Firm press release; self-reported and promotional |
| Active portfolio status | Public document | Firm website; self-reported |
| Platform founding date | Public document | Press release; note vehicle vs. organic founding distinction |
| Deal close date | Public document | Press release or independent deal coverage |
| Strategic pattern observation | Public inference | Conclusion drawn from multiple disclosed deals |
| Revenue or EBITDA estimate | Private inference | No public financial data for private companies |
| AI deployment fit assessment | Private inference | Analyst judgment; label as speculation |
| Departure or absence of a named person | Public document + gap | Last known source is public document; absence itself is a finding |

---

## Public Document

### Definition summary

A claim is a public document when it is directly stated in a named, retrievable document that
exists in the public record and was published by a named organization. The document does not need
to be independent or unbiased to qualify as a public document. Self-reported documents (ADV
filings, press releases, firm websites, company websites) are public documents. Their source type
is public document; their reliability affects confidence band, not source type.

The key test: can a reader go find the document and read the claim themselves? If yes, it is a
public document.

### Examples from the Example Capital v1 dossier

---

**Example PD-1: Headquarters address from ADV filing**

Claim: "The firm is headquartered at 100 Example Avenue, 12th Floor, New York, NY."

Source type: public document

Reasoning: This claim is directly stated in the SEC ADV filing, which is a named, retrievable
document in the public record. Any reader can pull the ADV from the SEC's EDGAR database and
read the address themselves. The source type is public document regardless of whether the address
is independently corroborated. The confidence band (high, because the address is also on the firm
website and ADV-listed addresses carry regulatory consequence) is a separate determination.

Correct label: *(confidence: high | source: public document — SEC ADV filing and firm website,
both accessed June 2026)*

---

**Example PD-2: Regulatory AUM from ADV filing**

Claim: "Example Capital manages approximately $3.1 billion in regulatory assets under management."

Source type: public document

Reasoning: The figure is directly stated in the ADV filing. It is a public document regardless
of the fact that it is self-reported and may lag by up to 90 days. Self-reporting does not change
the source type; it lowers the confidence band to medium. This is a common miscategorization: the
v1 dossier treated this as high confidence, which is wrong, but the source type of public
document is correct.

Correct label: *(confidence: medium | source: public document — SEC ADV filing, self-reported,
lag up to 90 days; regulatory AUM is distinct from committed or invested capital)*

---

**Example PD-3: Sector focus stated on firm website**

Claim: "Example Capital invests exclusively in three sectors — Healthcare Services, Essential Services,
and Specialty Manufacturing."

Source type: public document

Reasoning: This is directly stated on the firm website. The firm website is a public document: it
is named, retrievable, and published by a named organization. The fact that it is self-reported
by the subject firm affects confidence (medium, because the firm controls its own website) but
does not change the source type. The claim is not an inference; it is a direct statement the
reader can verify by visiting the site.

Correct label: *(confidence: medium | source: public document — firm website, self-reported,
accessed June 2026)*

---

**Example PD-4: Partner role from firm press release**

Claim: "Morgan Partner is Managing Partner; he joined in 2007 and was named Managing Partner
in 2020."

Source type: public document

Reasoning: These details are stated in firm press releases, which are named, retrievable public
documents. The source type is public document. The confidence band is medium because the firm is
the sole author of its own press releases and there is no independent corroboration of the tenure
dates. The v1 dossier labeled this high confidence, which is a confidence error, not a source
type error.

Correct label: *(confidence: medium | source: public document — firm press release, self-reported)*

---

**Example PD-5: Portfolio company operational metrics from company website**

Claim: "Example Imaging has 110 centers across 30 states and over 300 affiliated
radiologists."

Source type: public document

Reasoning: These figures appear on the Example Imaging website, which is a public document published by
a named organization. A reader can visit the site and read the claim. The source type is public
document. The confidence is medium because the figures are company-reported marketing claims, not
audited data. The v1 dossier labeled this high confidence, which is wrong, but the source type
designation of public document is correct.

Correct label: *(confidence: medium | source: public document — Example Imaging website, company-reported,
not independently verified, accessed June 2026)*

---

**Example PD-6: Absence of a named person as a public document finding**

Claim: Robin Partner does not appear in any current firm materials.

Source type: public document (absence finding)

Reasoning: The last known reference to Partner is a 2020 firm press release, which is a public
document. His absence from all current public documents (firm website, recent press releases, ADV
personnel disclosures) is itself a finding grounded in the public record. The correct treatment
is to cite the last known public document, note the date, and state that no subsequent public
document references him. This is not a private inference; it is an observation about what the
public record does and does not contain.

Correct label: *(confidence: low | source: public document — last reference: firm press release,
2020; absent from all current firm materials reviewed June 2026; current status unverified)*

---

## Public Inference

### Definition summary

A claim is a public inference when it is a conclusion drawn by connecting evidence across two or
more public documents, where no single document states the conclusion directly. The reasoning
chain must be shown in the dossier: a reader should be able to follow the evidence from the
public documents to the conclusion without needing non-public knowledge.

Public inference is not speculation. It is reasoned analysis grounded entirely in the public
record. The confidence band for a well-reasoned public inference with strong underlying documents
is medium. For a public inference that requires more extrapolation or rests on weaker documents,
it is still medium but should note the inferential basis explicitly.

The key test: could a reader reconstruct this conclusion using only the public documents cited,
without any non-public knowledge or industry experience the reader would not already have?
If yes, public inference. If non-public knowledge is required, private inference.

### Examples from the Example Capital v1 dossier

---

**Example PI-1: Deal size range inferred from portfolio**

Claim: "Based on portfolio company profiles and the firm's stated middle-market focus, target
businesses appear to operate in the $25 million to $500 million revenue range."

Source type: public inference

Reasoning: The firm does not publicly state a revenue range. The $25M to $500M estimate is drawn
from observing the publicly disclosed portfolio companies and their approximate scale. Any reader
with access to the same public sources could follow the same reasoning. No non-public knowledge
is required. The source type is public inference.

Note: the wide range ($475M span) means this inference has limited precision as a targeting
signal. The confidence is low because the extrapolation is large and the range is not useful as
stated. A correctly labeled public inference can still carry low confidence if the underlying
evidence is weak or the gap between evidence and conclusion is large.

Correct label: *(confidence: low | source: public inference — drawn from portfolio company
profiles and middle-market positioning statements on firm website; note: range spans lower, core,
and upper middle market and carries limited precision as a targeting signal)*

---

**Example PI-2: Strategic pattern observation**

Claim: "The clustering of recent deal activity across fuel distribution, packaging, and
healthcare services suggests the firm is deepening sector concentration rather than broadening."

Source type: public inference

Reasoning: This conclusion is drawn from observing multiple publicly disclosed platform
investments and their sectors. No single press release or website page states that the firm is
deepening concentration; that is the analyst's conclusion from the pattern. But any reader with
access to the same press releases could observe the same pattern and draw the same conclusion.
The reasoning chain is transparent and the evidence is fully public. Source type is public
inference, not private inference.

Correct label: *(confidence: medium | source: public inference — based on review of disclosed
platform investments as of June 2026)*

---

**Example PI-3: Add-on acquisition strategy inferred from portfolio structure**

Claim: "Example Capital explicitly targets industries with fragmented competitive landscapes, acquires a
platform, and then builds scale through bolt-on deals."

Source type: public document, not public inference (this is a distinction worth noting)

Reasoning: This claim appears to be an inference about strategy, but it is actually directly
stated on the firm website and in press releases. When a claim that looks like an analytical
conclusion is in fact directly stated in a public document, the source type is public document,
not public inference. This example is included as a reminder to check whether the firm has stated
something directly before classifying it as inferred.

The error to avoid: labeling a claim as public inference because it sounds analytical, when the
firm actually stated it. That mislabeling makes the claim appear less certain than it is. Check
the source before assigning the type.

Correct label: *(confidence: medium | source: public document — firm website and press releases,
self-reported strategic framing, accessed June 2026)*

---

**Example PI-4: Fund raise positioning inferred from multiple signals**

Claim: "Combined with headcount growth and a high deployment pace, the rebrand suggests Example Capital
is positioning for a new flagship fund raise."

Source type: public inference

Reasoning: No single source states that Example Capital is preparing to raise a new fund. This conclusion
connects three observable public signals: a rebrand, reported headcount growth, and disclosed
deal activity. Each underlying signal is a public document. The connection between them and the
fund raise conclusion is the analyst's reasoning, but it is reasoning a reader could follow using
only the same public documents. Source type is public inference.

Note: the confidence is low rather than medium here because the inferential leap from "rebrand
plus deal activity" to "fund raise positioning" requires judgment about PE firm behavior that not
every reader would share. The reasoning chain is public but the conclusion is more speculative
than Example PI-2. This illustrates that public inference can carry low confidence when the gap
between evidence and conclusion is large, even if the underlying sources are public documents.

Correct label: *(confidence: low | source: public inference — drawn from rebrand announcement,
headcount figures, and disclosed deal activity; fund raise timing is unconfirmed)*

---

## Private Inference

### Definition summary

A claim is a private inference when it requires assumptions, estimates, or knowledge that no
public document supports. This includes financial estimates derived from industry benchmarks,
conclusions that require non-public knowledge to reach, and analyst judgments that go beyond
what any combination of public documents would support.

Private inference is not a disqualifying label. Private inference claims are acceptable in a
dossier when they are clearly labeled and when the basis for the inference is stated. What is not
acceptable is a private inference presented as a public document claim or as a public inference.

Speculation is a subcategory of private inference. Label a claim as speculation when the basis
is especially thin, when the conclusion reflects the analyst's own judgment rather than any
sourced evidence, or when a skeptical reader would find the connection between evidence and
conclusion difficult to follow even with full context.

### Examples from the Example Capital v1 dossier

---

**Example PRI-1: Revenue and EBITDA estimates for private companies**

Claim: Any estimate of a specific portfolio company's revenue or EBITDA derived from industry
benchmarks, headcount, or comparable company analysis.

Source type: private inference

Reasoning: Private equity portfolio companies do not file public financials. Any revenue or
EBITDA figure for a private portfolio company that is not directly stated in a public document
is a private inference, regardless of how well-reasoned the underlying analysis is. The
estimate may use publicly available comp data, but the application of that data to a specific
private company requires assumptions that no public document supports. Source type is private
inference and the confidence band is low.

Worked example: "Example Energy, founded in 2019 with operations across 12 states, likely
generates $150M to $300M in revenue based on fuel distribution industry comps and estimated
customer count."

Correct label: *(confidence: low | source: private inference — no public financial data
available; estimate based on industry comps and operational scale)*

---

**Example PRI-2: AI deployment fit assessment**

Claim: "The firm's concentration in labor-intensive, recurring-revenue businesses makes it a
natural candidate for operational AI deployment."

Source type: private inference — speculation

Reasoning: This claim does not follow from any public document. It reflects the analyst's
judgment about which business characteristics make a firm suitable for QofAI's services. That
judgment draws on knowledge of QofAI's methodology and AI deployment patterns that are not in
the public record. No reader could reconstruct this conclusion from public sources alone; it
requires access to QofAI's internal framework. Source type is private inference and it should
be labeled speculation because the basis is the analyst's own assessment, not any evidence chain.

This is also the most self-serving claim in the dossier given QofAI's business purpose. A
skeptical reader — an LP, a competing firm, a potential client — would want to know that this
observation comes from the analyst, not from the research. Correct labeling makes that
transparent.

Correct label: *(confidence: low | source: private inference — speculation; reflects analyst
judgment about AI deployment fit, not a finding from public research)*

---

**Example PRI-3: Motivation inference beyond what public signals support**

Claim: "The rebrand from a 30-year-old institutional identity is an unusual move for a firm of
this maturity... it suggests Example Capital is positioning for a new flagship fund raise and wants a
differentiated identity in a crowded middle-market PE market."

Source type: the first clause is public inference; the fund raise and differentiation
conclusions are private inference

Reasoning: Observing that the rebrand is unusual for a firm of this maturity is a public
inference: a reader can look at the firm's history and the rebrand announcement and reach that
conclusion independently. But concluding that the motivation is fund raise positioning and market
differentiation requires knowledge of how PE firms typically use rebrands, what LP dynamics are
in the current fundraising environment, and strategic judgment that goes beyond what any public
document states. That portion is private inference.

This example illustrates a claim that contains both a public inference component and a private
inference component. When a single sentence spans both types, split the claim or label the whole
sentence at the weaker type (private inference) and note the distinction.

Correct label for the full claim: *(confidence: low | source: private inference — motivation for
rebrand is analyst interpretation; the rebrand itself is public document; fund raise timing is
unconfirmed speculation)*

---

**Example PRI-4: Crunchbase role claim — the boundary between public document and private
inference**

Claim: "Sam Partner is listed as Co-Founder and Executive Chairman on Crunchbase."

Source type: public document (weak), not private inference

Reasoning: The Crunchbase entry is technically a public document: it is named, retrievable, and
published by a named platform. The source type is public document. But because Crunchbase is
user-edited, the confidence is low and the claim should be flagged as unverified. This is a case
where source type and confidence band diverge significantly: public document at low confidence
is a meaningful combination that the source-typer and confidence-scorer skills must handle
without collapsing into each other.

The error to avoid: calling this private inference because the sourcing is weak. Weak sourcing
affects confidence, not source type. A retrievable public document with weak reliability is still
a public document at low confidence, not a private inference.

Correct label: *(confidence: low | source: public document — Crunchbase entry, user-edited, not
corroborated by any primary source; note: Partner does not appear in current firm website, press
releases, or ADV; current role and relationship to firm are unverified)*

---

## Labeling Errors Summary

The following table summarizes every source type miscategorization or boundary case in the
Example Capital v1 dossier. The source-typer skill should treat these as reference cases.

| Claim | v1 Source Label | Correct Source Type | Error or Note |
|-------|----------------|---------------------|---------------|
| $3.1 B regulatory AUM | public document | public document | Correct type; confidence was wrong (high should be medium) |
| Sector focus (stated on website) | public document | public document | Correct |
| Add-on acquisition strategy | public inference | public document | Firm stated it directly; inference label understated certainty |
| Deal size range ($25M to $500M) | private inference | public inference or private inference | Borderline; if drawn from portfolio observation, public inference; if from analyst knowledge alone, private inference |
| "Notably high pace" comparative | unlabeled | private inference — speculation | Editorial judgment presented without any source label |
| Sam Partner current role | public inference | public document (low confidence) | Crunchbase is a public document; weak reliability affects confidence, not source type |
| AI deployment fit | private inference | private inference — speculation | Correct type; should also be labeled speculation explicitly |
| Fund raise positioning | public inference | public inference (borderline private) | Motivation inference requires judgment beyond public documents; confidence should be low |
| Robin Partner absence | not labeled | public document (absence finding) | Absence from public documents is itself a public document finding |

---

## Rules Derived from These Examples

These rules are distilled from the examples and errors above. The source-typer skill applies
them in order.

1. Source type and confidence band are independent. A public document can carry low confidence
   (user-edited, self-reported, stale). A private inference always carries low confidence. Do
   not conflate weak reliability with private inference.

2. Self-reported public documents are still public documents. An ADV filing, a firm press
   release, and a company website are all public documents regardless of who controls their
   content. Self-reporting affects confidence, not source type.

3. If the firm stated it, it is a public document, not a public inference. Check whether a
   claim that looks analytical is actually directly stated somewhere before labeling it as
   inferred. Mislabeling a stated claim as inferred makes it appear less certain than it is.

4. Public inference requires a visible reasoning chain. If you cannot write out the steps
   from public documents to conclusion in the dossier, the claim is private inference, not
   public inference. The reasoning chain is what distinguishes them.

5. Absence from public documents is a public document finding. When a named person or firm
   does not appear in current public materials, that absence is a finding grounded in the
   public record. Label it as such with the last known public document and the date of the
   gap observation.

6. Motivation and intent are almost always private inference. Concluding why a firm did
   something (rebranded, hired, exited) from public signals alone is rarely a clean public
   inference. The gap between observable action and inferred motivation almost always requires
   judgment that goes beyond what the public record supports.
