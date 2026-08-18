# Trust & confidence — how DronaCharya decides what to claim

The product promise is *evidence-first*: every answer is labeled with where
it came from and how much to trust it, and those labels are computed, not
asserted.

## Two score scales, two thresholds

Retrieval produces scores on one of two incompatible scales:

| Scale | When | Range | Threshold |
|---|---|---|---|
| Reranked (cross-encoder, sigmoid-normalized) | `rerank = "on"` (default) or `"auto"` with CUDA | 0..1, calibrated | `retrieval.min_relevance` (0.30) |
| Raw RRF fusion | `rerank = "off"` | ~0.015–0.033, rank-based | `retrieval.min_confidence` |

Raw fusion scores are rank arithmetic — the nearest neighbor of *any* query
exists, so they cannot express "nothing matches." That's why reranking
defaults to on (the model is small enough for CPU) and why the thresholds
are separate: comparing one number against both scales was a bug class.

`min_relevance` is tuned against the golden evaluation set
(`tests/test_eval_retrieval.py`): it must accept ≥90% of covered questions
while refusing 100% of off-corpus ones. Change the threshold → run the eval.

## The answer ladder (quick answers)

1. **KB path** — retrieval clears the gate AND the model doesn't reply
   `NOT_IN_KB`. Label: *from your knowledge base*, with every cited source.
2. **Grounded web path** — your SearxNG found pages, we fetched them, the
   model answered *from* them, and the cited URL is one of them. Then a
   claim-to-passage check (cross-encoder) verifies the cited page actually
   supports the answer text. Only this path can be **high confidence**, and
   only high-confidence answers auto-save.
3. **Model memory** — no grounding. Confidence is forced low, cited URLs
   are fetch-verified or dropped, and by default the CLI refuses with
   "No verified answer" (`--guess` shows it; saving asks you first).

"Confidence: high" therefore means: *fetched + cited + support-verified* —
never a model's self-assessment. Model-reported confidence is treated as a
hint and demoted whenever the evidence chain is incomplete.

## Citations

The server computes which context items an answer actually cited and ships
the indices with the result; every client (CLI, web, extension, future
mobile) renders the same set. Uncited retrieval candidates are context the
model rejected — listing them would misattribute the answer, so no surface
does.
