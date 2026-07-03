# RAG shortcomings

*Design notes: domain-specific pitfalls, the architecture that mitigates them, and a technical reference appendix.*

-----

## Overview

This document covers a Retrieval-Augmented Generation (RAG) system whose corpus is real parents’ stories and use cases, used to support other parents. The domain has properties that make generic RAG advice only half-applicable: advice is highly context-dependent (a strategy that is right for a toddler can be wrong or dangerous for a newborn), much of the content is anecdotal rather than evidence-based, and some queries touch on safety-critical or medical territory.

It is organized in three parts:

1. **Shortcomings** — what a parenting RAG system must be aware of.
1. **Architecture** — how to wire the pipeline so each shortcoming is mitigated.
1. **Appendix** — a technical reference for the retrieval/ranking concepts involved, logically ordered.

-----

# Part 1 — Shortcomings to Be Aware Of

### 1. Developmental-stage / age sensitivity

Parenting advice lives or dies on context. A sleep strategy for a 4-month-old can be inappropriate, or unsafe, for a newborn. Pure semantic similarity will happily return a beautifully relevant story about the *wrong* developmental stage, because the embedding captures topical similarity (“sleep,” “crying”) while missing the age constraint that actually determines correctness.

### 2. Anecdote-as-advice (survivorship bias)

This is the central trap. RAG retrieves one parent’s story, and the model generalizes it into a recommendation. You are surfacing what someone *said* worked for *their* child — not what is safe or effective across children. Survivorship bias is effectively baked into the architecture: the corpus over-represents memorable outcomes and confident narrators.

### 3. Safety-critical content

Sleep position, feeding, medication, choking, fever, allergic reactions, developmental red flags, and signs of postpartum depression are areas where a confident-but-wrong synthesis can cause real harm. Anecdotes are the wrong source class for these questions, and an emotionally distressed parent may phrase an emergency casually.

### 4. Stale norms / shifting guidance

Parenting consensus shifts over time, and an older corpus can encode advice now considered unsafe. Without recency or validity signals, retrieval treats a story from many years ago as equal to current consensus.

### 5. Retrieval misses

The right story exists in the corpus but does not surface in the candidate set — because of chunking, embedding domain mismatch, or vocabulary gaps between how the parent asked and how the story was written. Nothing downstream (reranking, a strong LLM) can recover a document that never made the candidate list.

### 6. Hallucinated synthesis

Even with good retrieved context, the generation step can invent specifics, merge two incompatible stories, or state an anecdote as general fact.

### 7. Embedding domain mismatch

Off-the-shelf embedding models are trained on general web text and may not cleanly separate parenting-specific concepts, slang, and verbatim terms (“witching hour,” “purple crying,” “cluster feeding,” “sleep regression”).

### 8. Cultural and demographic bias

Whatever stories you collected reflect the population that submitted them. Advice norms vary widely across cultures, family structures, and circumstances; an unrepresentative corpus quietly narrows the guidance.

### 9. Privacy and data sensitivity

User-submitted stories contain PII (names, ages, locations, health details), and the *queries themselves* are highly sensitive — a parent asking about a child’s health or their own mental state is sharing personal data that demands care in logging, storage, and retention.

### 10. Evaluation difficulty

There is no clean ground truth for “good parenting advice.” Standard retrieval metrics (precision/recall, NDCG) only get you partway, because a retrieval can be topically perfect yet age-inappropriate, unsafe, or poorly hedged.

-----

# Part 2 — How to Wire the System Around These

The strategy is a multi-stage pipeline where each stage is responsible for specific failure modes. The throughline: **filter on hard constraints early, search broadly, fuse and re-rank for precision, and route safety-critical questions away from anecdotes entirely.**

### A. Structured metadata + pre-filtering → mitigates (1), (4), (8)

Tag every story at indexing time with structured fields and filter on them *before* semantic search runs:

|Field             |Purpose                                                    |
|------------------|-----------------------------------------------------------|
|`child_age_range` |Hard constraint; prevents wrong-stage retrieval (1)        |
|`topic`           |Coarse routing and filtering                               |
|`family_structure`|Surfaces relevant context, addresses bias (8)              |
|`evidence_level`  |`anecdotal` vs `expert_reviewed`; feeds output labeling (2)|
|`outcome`         |Did it actually work, per the narrator?                    |
|`date / validity` |Down-weight or exclude stale norms (4)                     |

Pre-filtering converts a soft preference (“ideally same-age”) into a hard guarantee.

### B. Smart chunking (small-to-big) → mitigates (5)

Stories are narratives with an arc; fixed-size chunking shreds them mid-thought. Use **parent–child (small-to-big) indexing**: embed short retrieval units — ideally a `situation → what they tried → outcome` summary — but feed the *full* story to the generator. You retrieve on tight, high-signal units and generate on complete context.

### C. Contextual retrieval (auto-context per chunk) → mitigates (5), (7)

Before embedding/indexing each chunk, prepend an LLM-generated context line situating it in its parent story (e.g. “From a story about sleep-training a 6-month-old with gradual extinction; …”). This restores the referent a bare chunk loses and overlaps usefully with the metadata in (A). See Appendix A7.

### D. Hybrid retrieval: dense + BM25 → mitigates (5), (7)

Run semantic (dense vector) search and keyword (BM25) search in parallel. Dense captures the emotional gist of a story; BM25 catches verbatim domain terms that embeddings smear into neighbors. Neither alone is sufficient. See Appendix A2–A4.

### E. Fusion with Reciprocal Rank Fusion → supports (D)

Merge the two ranked lists with RRF, which combines by rank position rather than incomparable raw scores. Documents both retrievers liked rise; single-channel hits still get partial credit. See Appendix A4.

### F. Constraint-aware reranking → mitigates (1), (2)

Run a cross-encoder reranker over the fused candidates to do the expensive, query-aware precision pass. This is where age-appropriateness and on-topic-ness are enforced, and where an LLM-based reranker can be instructed to prefer expert-reviewed over anecdotal content. See Appendix A5–A6.

### G. Query rewriting + conversational profile → mitigates (5)

Parents ask vague, emotionally loaded questions (“she won’t stop crying and I can’t do this”). Rewrite/expand these into retrievable queries and decompose multi-part questions. Carry a lightweight child profile (age, known issues) across the conversation so you stop re-asking and can auto-apply the age filter in (A).

### H. Intent routing → mitigates (2), (3)

Classify each query and route accordingly:

- **“I want to feel less alone”** → retrieve a resonant story (anecdote is the *right* source here).
- **“What’s the actual guidance?”** → retrieve vetted, factual, expert-reviewed content.
- **Safety / medical** → do **not** answer from anecdotes; see (I).

### I. Safety guardrails + escalation → mitigates (3)

For sleep position, feeding, medication, choking, fever, red flags, and maternal mental health: route to vetted, current, expert-reviewed sources or explicitly defer to a clinician. Build escalation language for anything that reads as an emergency or a parent in crisis. This is a hard architectural boundary, not a tuning knob.

### J. Anecdote labeling in output → mitigates (2)

When the answer draws on a parent’s story, surface that provenance (“One parent shared that…”) rather than letting an anecdote read as authoritative guidance. The `evidence_level` field from (A) drives this directly.

### K. Rubric-based evaluation → mitigates (10)

Supplement retrieval metrics with a rubric scored by humans or an LLM judge: Was it age-appropriate? Safe? Appropriately hedged? Warm in tone? Correctly labeled as anecdotal? Track these alongside NDCG/recall, and always evaluate the reranker *on and off* — it only helps when recall is already high (see Appendix A5).

### End-to-end flow

```
                         ┌─────────────────────────────┐
   User query  ──▶  (G) Rewrite + profile  ──▶  (H) Intent router
                                                     │
              ┌──────────────────────────────────────┼─────────────────────┐
              ▼                                       ▼                     ▼
        "feel less alone"                    "actual guidance"        safety / medical
              │                                       │                     │
              └──────────────┬────────────────────────┘                     ▼
                             ▼                                       (I) vetted sources /
                  (A) metadata pre-filter                              defer to clinician
                  (age, topic, recency)                                 + escalation
                             │
              ┌──────────────┴───────────────┐
              ▼                               ▼
     Dense vector search              BM25 keyword search        ◀── (B,C) small-to-big
              └──────────────┬───────────────┘                       + contextual chunks
                             ▼
                  (E) RRF fusion → candidate set
                             ▼
                  (F) cross-encoder rerank (constraint-aware)
                             ▼
                  Full parent stories → LLM generation
                             ▼
                  (J) anecdote labeling + hedging
                             ▼
                  (K) logged for rubric evaluation
```

-----

# Appendix — Technical Reference

*Ordered from foundations → retrievers → fusion → reranking → indexing enhancement.*

## A1. Bi-encoders vs. cross-encoders (the underlying split)

Almost every concept below is an instance of one of two architectures:

- **Bi-encoder:** query and document are embedded **separately** into vectors and compared with a cheap similarity (cosine). Document vectors are precomputed once, so it scales to millions of items — but the document never “sees” the query, so its embedding is a lossy, general-purpose summary. This is what first-stage **dense vector search** uses.
- **Cross-encoder:** query and document are fed through one model **together**, so every query token attends to every document token, producing a far more accurate relevance score. No precomputation is possible, so it runs at query time on a small candidate set only. This is what **rerankers** use.

The whole point of a two-stage pipeline is to get the recall of a cheap bi-encoder and the precision of an expensive cross-encoder without paying the cross-encoder’s cost over the full corpus.

## A2. BM25 (lexical / keyword retrieval)

BM25 (“Best Matching 25,” from the 1990s Okapi system) is the standard keyword retrieval algorithm — the engine behind Elasticsearch, OpenSearch, and Lucene, and the lexical half of hybrid search. It is a refinement of TF-IDF, uses no embeddings or training, and is a bag-of-words method (word order ignored). Three intuitions drive it:

- **Term frequency, saturating:** more occurrences help, but with diminishing returns, so keyword spam can’t dominate.
- **Inverse document frequency (IDF):** rare terms are more informative than common ones.
- **Document-length normalization:** long documents are penalized so they don’t match by sheer size.

$$
\text{BM25}(D,Q) = \sum_{t \in Q} \text{IDF}(t)\cdot \frac{f(t,D),(k_1+1)}{f(t,D) + k_1!\left(1 - b + b,\dfrac{|D|}{\text{avgdl}}\right)}
$$

where $f(t,D)$ is term frequency, $|D|$ the document length, $\text{avgdl}$ the corpus average length, $k_1$ controls TF saturation (~1.2–2.0), and $b$ controls length normalization (~0.75; $b=0$ disables it).

**Strength:** fast, cheap, interpretable, excellent at exact matches (names, jargon, verbatim parent terms). **Weakness:** zero semantic understanding — “infant won’t sleep” and “baby keeps waking up” share no words and score as unrelated. This is exactly the gap dense search fills.

## A3. Dense vector search (semantic retrieval)

A bi-encoder embedding model maps text to vectors such that semantically similar texts land near each other; retrieval is nearest-neighbor search over those vectors. **Strength:** captures meaning across different wording. **Weaknesses:** smears specific/rare terms into nearby concepts, and off-the-shelf models may not match a specialized domain — motivating both hybrid search and domain-aware embeddings.

## A4. Hybrid search & Reciprocal Rank Fusion (RRF)

Because BM25 and dense search have complementary strengths, run both and merge. The merge problem: their scores live on different scales (cosine ~0–1 vs. unbounded BM25), so adding them is brittle. **RRF** sidesteps this by using only **rank positions**, which are comparable across systems — it is a *fusion* step, not a reranking step, and uses no model or training:

$$
\text{RRF}(d) = \sum_{i} \frac{1}{k + \text{rank}_i(d)}
$$

For each document, sum a contribution from each list it appears in, where $\text{rank}_i(d)$ is its position in list $i$ and $k$ is a smoothing constant (the original paper used 60). A higher $k$ flattens the influence of top ranks so no single list dominates on its #1 hit; a lower $k$ makes top ranks more decisive. Documents both retrievers ranked highly rise to the top; single-channel hits still get partial credit. It is unsupervised, nearly free, and a strong baseline — it’s what many “hybrid mode” toggles in vector databases do internally.

## A5. Reranking (two-stage retrieval)

A reranker re-scores the fused candidate set (e.g. top ~100–150) with an expensive, query-aware model and returns the top few (e.g. 5–10) for generation. Search broad and cheap, then judge narrow and expensive. Types:

- **Cross-encoders** — the workhorse; query+document through one transformer, one relevance score. Most accurate per pair, run only on candidates. *(Pointwise.)*
- **Late-interaction (ColBERT)** — token-level embeddings with a MaxSim match; between bi- and cross-encoder in cost and precision.
- **LLM-based rerankers** — prompt a model to score or order candidates; can be **listwise** (orders several at once, reasoning relatively) and can take instructions (“prefer age-appropriate, recently-validated content”). Most flexible, slowest, priciest.

Scoring styles: **pointwise** (score each independently), **pairwise** (which of two is better), **listwise** (order the whole set).

**Critical caveat:** a reranker only helps when recall is high but precision is low. It cannot rescue a correct document that never entered the candidate set, and it adds latency. Always evaluate it on/off on the same queries (NDCG@k, recall before vs. after, latency).

## A6. Cohere Rerank (a hosted cross-encoder)

Cohere is an AI company; its **Rerank** model is a hosted cross-encoder you call via API — the concrete product behind the cross-encoder category. You pass a query and a list of documents and get them back sorted by relevance with scores. It reads query and document together (cross-encoding); if their combined length exceeds the per-document context limit, the document is auto-chunked across inferences. It integrates into an existing pipeline in a few lines and reduces downstream cost by passing fewer, more relevant documents to the generator.

Version lineage: **Rerank 3.0 → 3.5 → 4.0**. Rerank 3.5 supports 100+ languages with a 4096-token context length and improved handling of constraint-bearing queries. **Rerank 4.0** (released December 2025) is the current top model, with state-of-the-art accuracy, 100+ language coverage, and strong performance on semi-structured data (emails, tables, JSON, code). As a rough latency guide, Rerank 3.5 adds roughly 80–150 ms on smaller chunks and more on large ones — worth measuring against your p99 budget.

## A7. Anthropic’s Contextual Retrieval (indexing-time enhancement)

Targets the chunking failure where a chunk loses its referent (e.g. a chunk says revenue “grew 3%” without naming the company or quarter). **Fix:** before embedding *and* before BM25 indexing, prepend a short (~50–100 token) LLM-generated context blurb that situates each chunk within its full document. The two halves are called **Contextual Embeddings** and **Contextual BM25**.

Reported results on Anthropic’s benchmarks (codebases, fiction, papers):

|Configuration             |Top-20 retrieval failure rate|Reduction|
|--------------------------|-----------------------------|---------|
|Baseline                  |5.7%                         |—        |
|Contextual Embeddings only|3.7%                         |35%      |
|+ Contextual BM25         |2.9%                         |49%      |
|+ Reranking               |1.9%                         |67%      |

This is why hybrid search, RRF, and reranking are usually discussed together — they compound. The main cost is one LLM call per chunk at indexing time, mitigated with **prompt caching** (the full document is cached once and reused across its chunks; Anthropic estimated roughly $1.02 per million document tokens). The other trade-off is ~50–100 extra tokens per chunk. For the parenting use case, the auto-generated context line (child age, topic, outcome) does much of the same work as structured metadata tagging — prose for retrieval, structured fields for filtering — and the two reinforce each other.

-----

*End of document.*