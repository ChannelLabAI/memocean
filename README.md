# MemOcean

> Precise retrieval · Work-first design · No GPU required — a persistent knowledge base for multi-Agent Chinese-language collaboration.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status: Production](https://img.shields.io/badge/Status-Production-green.svg)]()

---

## Table of Contents

- [Why We Built MemOcean](#why-we-built-memocean)
- [What MemOcean Is](#what-memocean-is)
- [Multi-Agent Collaboration Design](#multi-agent-collaboration-design)
- [Architecture: Ocean Metaphor + Dual-Engine Retrieval](#architecture-ocean-metaphor--dual-engine-retrieval)
- [CLSC Chinese Compression Engine](#clsc-chinese-compression-engine)
- [Acknowledgments](#acknowledgments)

---

## Why We Built MemOcean

When we first saw [MemPalace](https://github.com/milla-jovovich/mempalace), we were thrilled -- someone finally built long-term memory for LLMs, and it was by Milla Jovovich herself.

During testing, we ran into two problems that turned out to be hard to work around:

**1. The Chinese Token Tax**

Mainstream tokenizers are brutally inefficient with Chinese -- the same semantic content costs 2-3x more tokens in Chinese than in English. MemPalace's original design assumes English whitespace tokenization (`text.split()` for token splitting, ALL-CAPS abbreviations as entities, LIKE full-string matching), all of which break completely in Chinese. We tried dictionary-based compression but hit a dead end -- the replacement tags ended up costing *more* BPE tokens than the original text. We eventually solved it by pivoting to skeleton lossy summaries.

**2. Multi-Agent Memory Drift**

We run a dozen Agents in daily production -- assistants, builders, reviewers, designers, each with their own role. Every Agent's session memory is isolated, and after a few days their "memories" start to diverge. One Agent remembers last week's decision, another doesn't. Some reference stale information, others hold contradictory context.

This isn't a prompt engineering problem. It's an architecture problem: **we needed a single source of truth -- a persistent knowledge base that all Agents share as their common factual foundation**.

MemOcean is what we built to solve both of these.

---

## What MemOcean Is

MemOcean is a Chinese fork of MemPalace with three core changes:

1. **Chinese NER pipeline** -- jieba POS tagging replaces `text.split()` for entity extraction, handling Traditional Chinese + Simplified Chinese + English code-switching
2. **Multi-Agent support** -- concurrent read/write by multiple Agents, with append-only write rules to prevent conflicts
3. **Ocean metaphor naming** -- from palace to ocean, a naming system that better reflects how knowledge actually flows

The knowledge base is built on [Obsidian](https://obsidian.md) vaults -- everything is Markdown + `[[wikilink]]`, readable and writable by both humans and Agents using the same toolset. No extra database or proprietary format required.

The shift from palace to ocean isn't just branding. A palace is static and enclosed; an ocean is fluid and open. When multiple Agents write to the knowledge base concurrently, the state of knowledge behaves more like ocean currents than rooms in a building.

---

## Multi-Agent Collaboration Design

This is the most fundamental difference between MemOcean and MemPalace. MemPalace was designed for a single LLM session; MemOcean was designed for multi-Agent workflows from day one.

### A Shared Knowledge System Across the Agent Team

Agents are organized by function -- Assistant (requirements analysis, task dispatch), Builder (development), Reviewer (code review, QA), and Designer (UI/UX). Each role can scale horizontally, with cross-Agent communication handled via [claude-telegram-bots](https://github.com/ChannelLabAI/claude-telegram-bots).

All Agents read from and write to the same Ocean directory. Any knowledge written by one Agent is immediately available to all others.

### Write Rules

The biggest risk with concurrent writes is conflicts. MemOcean eliminates this with three rules:

1. **Append-only** -- only append, never overwrite. Every write adds a timestamp and source tag at the bottom
2. **Source tagging** -- `<!-- appended by {agent_id} at {datetime} -->`, providing full traceability of who wrote what and when
3. **Periodic linting** -- automated deduplication, formatting cleanup, and cross-reference backfilling

No locks, no conflict resolution -- append-only eliminates write conflicts at the architectural level.

### Five Search Paths

Different scenarios call for different search strategies. MemOcean provides five paths:

| Path | Searches | Speed | Use Case |
|------|----------|-------|----------|
| `closet_search` | CLSC skeletons | Fast | Quick lookup: "Is there any material on X?" |
| `closet_get` | Original text verbatim | Medium | Full retrieval: "Give me the complete text on X" |
| `fts_search` | Cross-Agent messages | <10ms | History search: "Who said X, and when?" |
| `kg_query` | Temporal knowledge graph | Medium | Relationship query: "What's the relationship between X and Y?" |
| Direct read | Vault files | Fast | When you know the exact path |

The first thing an Agent does when it receives a task is to check the Ocean (we call this Step 0) -- look for relevant historical decisions or precedents to avoid duplicated work or contradictory decisions.

### Session Memory vs Persistent Knowledge

MemOcean strictly separates two types of memory:

- **Session memory** -- each Agent's own `session.json`, storing current work state and in-flight tasks. Isolated, clearable on restart
- **Persistent knowledge** -- the Ocean directory, shared by all Agents. Once written, knowledge persists permanently

This separation is the key to solving memory drift. Agents can have different session memories, but the underlying factual foundation (Ocean) is unified.

---

## Architecture: Ocean Metaphor + Dual-Engine Retrieval

### Ocean Naming System

MemPalace uses a palace metaphor (Palace > Wing > Room > Skeleton > Drawer). MemOcean uses an ocean metaphor, with the following mapping:

| Function | Ocean Name | Path | MemPalace Equivalent |
|----------|-----------|------|---------------------|
| Knowledge store | Ocean | `Ocean/` | Palace |
| Project category | Current (洋流) | `Currents/` | Wing |
| Subcategory | Reef (珊瑚礁) | Subdirectory under Current | Room |
| Compressed skeleton | Sonar (聲納) | `*.clsc` | Skeleton |
| Raw material | Seabed (海床) | `Seabed/` | Drawer |
| Insight cards | Pearl (珍珠) | `Pearl/` | Cards |
| Technical docs | Chart (海圖) | `Chart/` | Concepts |
| Research reports | Research | `Research/` | Research |
| Archive | Depth (深處) | `Depth/` | Archive |

### Directory Structure

```
Ocean/
├── Currents/
│   ├── ProjectAlpha/
│   │   ├── Sales/             # Reef: Sales
│   │   ├── Product/           # Reef: Product
│   │   └── Org/               # Reef: Internal org
│   ├── ProjectBeta/
│   └── ProjectGamma/
├── Pearl/                      # Insight cards (cross-project)
├── Chart/                      # Technical docs (cross-project)
├── Research/                   # Research reports (cross-project)
├── Seabed/                     # Raw material
├── Depth/                      # Archive
├── _schema.md                  # Write rules
└── _index.md                   # Auto-generated index
```

**Boundary principle**: project-bound content lives inside its Current (People, Companies, Deals, raw material); cross-project content lives at the top level (Pearl, Chart, Research). Currents are linked via `[[wikilink]]`, never by moving files.

### Dual-Engine Retrieval Architecture

MemOcean has two independent retrieval engines, each serving a different purpose:

```
Seabed (raw material) ──→ Sonar (machine index)
                         Teaches the Agent "what exists": fact location, fast retrieval

Various sources ──→ Pearl (distilled insight)
  ├── Conversations    Teaches the Agent "how to think": judgment frameworks, decision logic
  ├── Research
  ├── Meetings
  └── Insights from reading source material
```

- **Sonar** is machine compression -- automatically generates skeleton indexes (~9% token count) from Seabed source text, helping Agents find things fast
- **Pearl** is human distillation -- atomic insights (100-300 words) extracted from conversations, research, meetings, and work discussions, teaching Agents to reason with the boss's logic

The two exist independently and cross-reference each other via `[[wikilinks]]`. They are not in an upstream-downstream compression relationship.

### Search Pipeline: Hybrid Recall

Retrieval uses a three-stage hybrid recall pipeline — all API calls, no local GPU required:

```
keyword (FTS5 BM25)
  +                      ──→ merge top-K candidates ──→ Haiku LLM reranker ──→ ranked results
embedding KNN (API)
```

- **keyword**: FTS5 trigram + BM25, precise entity matching, <10ms
- **embedding KNN**: semantic vector nearest-neighbor search, fills semantic inference term gaps, runs via embedding API with no local GPU
- **Haiku reranker**: lightweight LLM ranking pass, more stable in work-context semantics than pure cosine similarity

---

## CLSC Chinese Compression Engine

**CLSC** (Chinese Lossy Summary Compression) is MemOcean's core engine, forked from MemPalace's AAAK skeleton format, with the entire NER + search pipeline rewritten for Chinese.

### Differences from Upstream AAAK

| Upstream AAAK Assumption | CLSC Chinese Implementation |
|--------------------------|----------------------------|
| `text.split()` for tokenization | jieba POS tagging with automatic NER |
| ALL-CAPS abbreviations as entities | Chinese entities use pinyin initials + token-aware gate |
| LIKE full-string matching | FTS5 trigram + BM25 ranking, fallback to OR-match |
| Fixed budget truncation | Content-proportional scaling (dynamically adjusts by source text length) |

### Skeleton Format

Each piece of source material is compressed into a single-line skeleton, stored as a `.clsc` file:

```
[SLUG|ENTITIES|topics|"key_quote"|WEIGHT|EMOTIONS|FLAGS]
```

### Performance Data

Real-world benchmarks on actual Chinese content (148 Obsidian vault documents):

| Metric | Value |
|--------|-------|
| Test corpus size | 148 documents |
| Original token count | 459,490 |
| Sonar token count | 43,392 |
| Overall compression ratio | **9.4%** |
| Token savings | **90.6%** |
| Average savings in search scenarios | **78.2%** |

The Sonar-first search path (read skeleton first, fetch full text on demand) saves roughly 78% of token consumption compared to reading full text directly.

### Search Optimization

We went through three iterations of search:

| Query Type | v1 ALL-match | v2 OR-match | v3 FTS5+BM25 |
|-----------|-------------|-------------|--------------|
| Structured queries | 50% | 89% | 89% |
| Natural language queries | 0% | 55% | 55% |
| Known-document queries | 85% | 95% | 95% |

v3 has the same hit rate as v2 (FTS5 automatically falls back to OR-match on miss), but ranking quality improved substantially -- BM25 pushes the most relevant document to top-1 instead of surfacing high-frequency but imprecise results. The improvement in top-1 accuracy is especially notable for English-language queries.

### Benchmark

MemPalace was designed for English; MemOcean was designed for Chinese work scenarios. Each system is benchmarked in its own language:

| | MemPalace AAAK skeleton | MemOcean Hybrid+Haiku |
|---|---|---|
| Benchmark | LongMemEval (English) | MADial-Bench (Chinese) |
| Hit@5 / R@5 | 84.2% | **87.5%** |
| Hit@1 | N/A | **68.1%** |

MemOcean outperforms MemPalace's English skeleton mode in Chinese by **+3.3pp** (Hit@5).

English cross-validation (LongMemEval): MemOcean Seabed+BM25 **90.5%** R@5 vs MemPalace AAAK **84.2%** (+6.3pp).

Large-scale stress test (CRUD-RAG): 20K Chinese news documents, **99%** hit rate, **14ms** latency.

### Known Limitations

To be upfront: CLSC currently has two known limitations:

1. **jieba Traditional Chinese precision** -- jieba's dictionary is primarily Simplified Chinese, with Traditional Chinese handled via statistical fallback. NER recall on Traditional Chinese has not been quantitatively baselined yet
2. **Cold-term coverage** -- the hybrid recall embedding path compensates for common semantic inference misses, but very rare terms or heavily abbreviated jargon (unseen by both paths) can still slip through. A query expansion layer is needed to fully address this

---

## Acknowledgments

MemOcean's architectural foundation comes from [MemPalace](https://github.com/milla-jovovich/mempalace) and its AAAK skeleton format. The memory palace metaphor, the drawer/closet dual-layer architecture, the core philosophy of lossy summaries -- these are all original designs by the MemPalace team.

What we built on top of that foundation is bringing it to the Chinese language and to multi-Agent collaboration scenarios. The naming shifted from palace to ocean, but the design philosophy underneath remains the same: **don't make the Agent read the full text before it can think -- give it a precise index and that's enough.**

---

## License

MIT
