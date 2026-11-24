---
hide:
- navigation
- toc
---

<div class="lc-hero" markdown>
<div class="lc-hero__text" markdown>

# SchemaCast

Turn text into a knowledge graph. You say what to look for, in a few lines of
YAML; every fact keeps the words it came from.
{ .lc-lead }

SchemaCast reads short texts (news, messages, notes, tickets) and writes the
entities, how they relate and what is said about them. Each text is one model
call, checked against your schema, so what lands in the graph has the shape you
asked for.

```bash
pip install schemacast
```

[Quick start](getting_started/quickstart.md){ .md-button .md-button--primary }
[How a cast works](concepts/index.md){ .md-button }

</div>
<figure class="lc-cast">
<svg viewBox="0 0 440 270" role="img" aria-labelledby="lc-cast-title" xmlns="http://www.w3.org/2000/svg">
<title id="lc-cast-title">A short text becomes typed vertices and edges of a governed schema; each one points back at the quote it was read from.</title>
<defs>
<marker id="lc-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
<path class="lc-cast__arrowhead" d="M0,1 L9,5 L0,9 z"/>
</marker>
</defs>
<rect class="lc-cast__band" x="226" y="28" width="206" height="214" rx="8"/>
<rect class="lc-cast__card" x="20" y="66" width="150" height="122" rx="5"/>
<g class="lc-cast__quote">
<path d="M34,88 H62"/>
<path d="M112,88 H150"/>
<path d="M34,136 H86"/>
<path d="M100,152 H134"/>
</g>
<g class="lc-cast__text-line">
<path d="M34,88 H150"/>
<path d="M34,104 H142"/>
<path d="M34,120 H118"/>
<path d="M34,136 H150"/>
<path d="M34,152 H134"/>
<path d="M34,168 H96"/>
</g>
<text class="lc-cast__caption" x="95" y="210" text-anchor="middle">A short text</text>
<g class="lc-cast__ground">
<path d="M62,88 C130,40 220,58 268,64"/>
<path d="M150,88 C220,96 300,124 344,134"/>
<path d="M86,136 C170,150 270,170 330,178"/>
<path d="M134,152 C190,180 230,206 266,207"/>
</g>
<path class="lc-cast__edge" d="M293,82 L346,124" marker-end="url(#lc-arrow)"/>
<path class="lc-cast__edge" d="M293,196 L346,160" marker-end="url(#lc-arrow)"/>
<circle class="lc-cast__vertex" cx="280" cy="70" r="14"/>
<circle class="lc-cast__vertex" cx="364" cy="142" r="21"/>
<circle class="lc-cast__vertex" cx="280" cy="206" r="14"/>
<text class="lc-cast__label" x="280" y="46" text-anchor="middle">Person</text>
<text class="lc-cast__label" x="392" y="114" text-anchor="middle">Company</text>
<text class="lc-cast__label" x="280" y="236" text-anchor="middle">Company</text>
<text class="lc-cast__small" x="330" y="92" text-anchor="start">CEO_OF</text>
<text class="lc-cast__small" x="326" y="212" text-anchor="start">ACQUIRED</text>
<text class="lc-cast__small" x="364" y="182" text-anchor="middle">city Austin</text>
<text class="lc-cast__caption" x="329" y="262" text-anchor="middle">Typed by the schema</text>
</svg>
</figure>
</div>

!!! note "Early development"
    SchemaCast is on PyPI (`pip install schemacast`) and its API may still change; see
    [Installation](getting_started/installation.md).

## In three steps

<div class="lc-columns" markdown>
<div markdown>

### 1. Install

```bash
pip install schemacast
```

</div>
<div markdown>

### 2. Say what to look for

```yaml
name: deals
types:
  Company:
    key: name
    fields: {name: str, city: str}
  Person:
    key: name
    fields: {name: str}
relations:
- Person CEO_OF Company
- Company ACQUIRED Company
```

</div>
<div markdown>

### 3. Cast your texts

```bash
schemacast cast --schema schema.yaml \
    --episodes notes.jsonl --out out/
```

One text per line in `notes.jsonl`; out comes the graph, with the quote behind
every fact.

</div>
</div>

[See it run, no key needed](getting_started/quickstart.md){ .md-button .md-button--primary }

## What it does

<div class="lc-columns" markdown>
<div markdown>

### One call, checked

The model sees only the slice of the schema the text is about, and replies in a
flat format of vertices, edges and quotes. Types, relations, properties,
values, identities and quotes are checked; a reply that fails gets a typed
correction, at most twice.

</div>
<div markdown>

### Identities, not guesses

A text's mentions are resolved by their keys, never guessed: a complete key
identifies an entity, an alternate key looks one up, and anything less waits,
staged with its reason. Only a text that introduces an entity creates it. Writing a cast twice leaves a database that upserts
unchanged.

</div>
<div markdown>

### Grounded and replayable

Every assertion carries its source, character span and quote, and what the
text says about it: when it holds, whether it is planned or hedged, and what
the text denies. Replies are cached by request, so a replay makes no request
and reproduces the graph byte for byte.

</div>
</div>

## What to read next

<div class="grid cards lc-next" markdown>

-   **[Installation](getting_started/installation.md)**

    Install the package and point it at a model.

-   **[Quick start](getting_started/quickstart.md)**

    Five news notes about companies and deals, cast in three steps.

-   **[How a cast works](concepts/index.md)**

    The pipeline from text to graph, step by step.

-   **[The benchmark](guides/benchmark.md)**

    Score a model against texts whose graphs are known.

</div>
