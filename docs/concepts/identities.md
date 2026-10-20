# Mentions and resolution

A short text rarely states a full key. "The customer with email
jo@example.com raised HD-4415" names a customer by an alternate key and a ticket
by its number, and says nothing else about either. SchemaCast therefore splits
the work in two:

1. **Cast** — the model reads the text into a **mention graph**: each entity the
   text names (a *mention*: its type, the properties the text states, where it
   states them) and the relations the text states between them. Nothing here
   decides which entity a mention is.
2. **Resolve** — graflo identifies each mention by its keys, without a model:
   it becomes an *entity* of the graph, or it is **staged** with the reason, to
   be resolved later.

A cast without a database resolves against the batch alone (`graph.json`,
`staged.jsonl`); `--db` resolves again with the database to look keys up in.

## The key contract

How a mention can be identified depends on its type's keys:

| Key source | Declared in the schema as | Primary key comes from |
|---|---|---|
| stated | `identity: [account]` | the text: every identity field |
| derived | `hash_identity_properties`, `identity_funnel` | a digest of every source field (or of one complete funnel branch) |
| minted | `assigned: true`, `blank: true` | SchemaCast, for an entity the text introduces |

**Alternate keys** (`secondary_identities`) find an entity but never create
one: an email, a handle, a phone number. Declare one for every name a text uses
for an entity it does not key. [Preparing a schema](../guides/schema.md#declare-keys)
maps keys from other schema languages to these.

## How a mention is resolved

The first rule that holds decides:

| The mention states | Outcome |
|---|---|
| a complete primary key | resolved: written with the properties the text states |
| a complete alternate key | looked up, first among the batch's resolved mentions, then in the database: exactly one entity resolves it; several stage it as `ambiguous`, none as `unmatched` |
| nothing, and its type is minted | minted when the text introduces the entity; staged as `keyless_reference` when it only refers to one |
| part of a key | staged as `partial_key` |
| no key field | staged as `no_key` |

A relation follows its endpoints: written when both resolve, otherwise held
with the staged mention it waits on. A mention's `introduces` (the model's
`mention: false`) matters only for a minted type; for every other type the key
decides.

**Partial keys never identify.** Nothing merges two vertices later, so a
guessed identity would stay a duplicate. A staged mention keeps what the text
states, so a later resolution — a re-run once the graph holds the entity, a
known entity, a person — writes it then.

**Connector-owned types are linked, never written.** When the schema comes in a
manifest, a type that a connector-fed resource ingests is owned by that
connector: a mention of it is looked up by its key and linked, and the text
writes nothing onto it. One the database does not hold yet is staged as
`owned_unmatched`, so a misread ticket number never becomes a ticket.

## The repair a key gap asks for

When the model says a text introduces an entity of a stated or derived type
but gives no complete key, validation asks once for the key, in case the reply
left out one the text states (`identity_insufficient`). The mention is kept
either way; resolution decides.

## An example

The five texts of `examples/tickets`, and where their mentions go:

| Text | Mention | Outcome |
|---|---|---|
| mail-1041 | Customer ACC-2291, Ticket HD-4411, Product SKU-77 | resolved by key |
| chat-2207 | Agent lena.k, Ticket HD-4411, Tag "hardware" | resolved by key (the tag's key is derived) |
| mail-1042 | Ticket HD-4415, HD-4411 | resolved by key; `DUPLICATE_OF` written |
| mail-1042 | Customer with email jo.park@example.com | `unmatched` until an entity with that email exists; `RAISED` held |
| note-88 | Comment | minted; `ON` HD-4411 written |
| note-88 | Ticket HD-4411 (referred to) | resolved by key; its stated `wait` is written |
| chat-2210 | none | an instruction, not a statement |

## Re-casting

Keys a text states and keys derived from them are the same on every cast, so
a re-cast resolves to the same entities. A minted key depends on the episode
and the model's `ref`: a replay reproduces it, a fresh model call that names
its refs differently mints another.

## Edges

An edge is identified by its endpoints and its relation. Give it `identities`
in the schema (`identities: [[source, target, since]]`) only when several
edges between the same two vertices must stay apart; its other properties are
written, never matched on.
