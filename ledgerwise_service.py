"""Confirmed service facts and separate communication policy."""
SERVICE_DESCRIPTION = "Ledgerwise is a US bookkeeping firm providing monthly bookkeeping for e-commerce brands."
PRICING_POLICY = "Pricing is discussed on a call. Never quote a price."

ORDINARY_REPLY_GROUNDING = (
    "Confirmed Ledgerwise facts and policy: " + SERVICE_DESCRIPTION + " " + PRICING_POLICY + "\n"
    "General bookkeeping knowledge: monthly bookkeeping generally means organizing and maintaining financial "
    "transaction records over time. Present this as a general definition. Do not turn it into a promise about "
    "Ledgerwise's specific tasks, deliverables, record accuracy, or business outcomes.\n"
    "Unconfirmed commitments: no specific process, reconciliation, reporting, integration, pricing package, "
    "fee calculation, discount, guarantee, or growth outcome is established. Answer confirmed questions directly. "
    "For each unconfirmed question, explicitly acknowledge that the requested detail cannot be confirmed and state "
    "the approved next step when one exists. Keep the acknowledgment separate from the next step, for example "
    "'I can't confirm [requested detail]. [Approved next step].' Do not add a rationale or explanation for an "
    "unknown detail unless the approved facts establish it. In verification, an explicit acknowledgment of an unconfirmed detail "
    "counts as addressing that question. Do not demand an answer the approved facts cannot support. A next step alone "
    "does not acknowledge the limitation. Reject invented or misleading claims and omitted questions that can be "
    "answered safely. Accept faithful paraphrases of the confirmed service and policy and general definitions that "
    "do not imply an unconfirmed commitment. Flag the precise claim or omission when rejecting."
)


EVIDENCE_RULES = (
    "The supplied Ledgerwise service description is the authoritative source for approved facts about Ledgerwise. "
    "Accept faithful statements of those facts without prospect citations, including that Ledgerwise is a US "
    "bookkeeping firm providing monthly bookkeeping for e-commerce brands. Do not count Ledgerwise facts as "
    "prospect personalization or include them in fact_ids. Every prospect-specific fact must be supported by saved "
    "research cited for that claim. Reject unsupported prospect facts and Ledgerwise claims beyond the approved "
    "description, including additional services, capabilities or guarantees. "
)
