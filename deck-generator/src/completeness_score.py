"""The completeness score (E7a, scoring half, folded in from E7b 2026-08-10).

One sentence: `data_completeness` is the share of the six packet fields the deck
needs that the paper actually supplied.

That is the whole rule. No weighting, no partial credit, no confidence
adjustment, and no second opinion about what counts as present: this module
reads `Packet.present`, which assembly decided, and divides. A field talked into
existence to lift a ratio is the failure both halves of E7a were written to
prevent, so the score has no way to reach back and change what assembly
produced. Assembly does not import this module and does not know it exists.

The gate is the existing one. `min_data_completeness` is a request option whose
contract default of 0.70 lives in `data_source_adapter.DEFAULT_OPTIONS`
(`proposal-data-request-CONTRACT.md` section 2), so the threshold is passed in
rather than restated here. Below it the contract returns `E_LOW_CONFIDENCE`
rather than a partial deck, and some opportunities landing there is the correct
outcome for an uneven corpus rather than a bug to fix.

One limit worth stating rather than working around. The adapter's `_passes_gates`
checks `min_confidence` as well, and a packet with no `confidence` band fails
closed. The provider has no measure of confidence beyond this ratio, so deriving
a band from it is a decision for whoever wires the provider in behind the seam
(E9) rather than something to invent here.
"""

from packet_assembly import ROSTER

E_LOW_CONFIDENCE = "E_LOW_CONFIDENCE"


def data_completeness(packet):
    """Fields present divided by fields the deck needs."""
    return len(packet.present) / len(ROSTER)


def gate(packet, min_data_completeness):
    """`None` when the packet clears the completeness gate, else the error code.

    The caller supplies the threshold from the request's own options, so this
    reports the existing gate's decision rather than holding a second copy of
    it.
    """
    if data_completeness(packet) < min_data_completeness:
        return E_LOW_CONFIDENCE
    return None
