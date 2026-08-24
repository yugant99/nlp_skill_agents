# Researcher-Supervised Transcript Revision Pilot

Status: bounded pilot contract

Protocol: `research-transcript-v1`

## What This Pilot Proves

The pilot demonstrates that a researcher can import an explicitly synthetic or
authorized de-identified transcript, run four bounded Luna specialists over each
deterministic chunk, review every proposed line change, and deliberately create a
new local transcript revision. The original source remains immutable and can be
restored as the active revision.

The four specialists are fixed:

1. speaker classification;
2. explicit timing and pause evidence;
3. verbatim spoken-content preservation; and
4. direct-identifier redaction plus explicit nonverbal cues.

Every specialist returns strict JSON. The local backend validates exact line
coverage and composes the candidate without another model call. A chunk therefore
plans exactly four inference calls and a job plans exactly:

```text
planned_call_count = chunk_count * 4
```

This is intentionally different from the short professor-demo lane, which has one
chunk and exactly four calls total.

## Honest Claim Boundary

This is a single-researcher, loopback-only, supervised pilot. It is not:

- authorization to process identifiable or otherwise sensitive research data;
- a de-identification service or a substitute for researcher review;
- a clinical, diagnostic, or treatment system;
- proof of transcript accuracy, domain validity, or inter-rater reliability;
- an authenticated multi-user research platform;
- an exactly-once remote billing guarantee;
- a production installer, Phase 4 completion, or Phase 6 completion.

The researcher identity stored by this lane is attributable workflow metadata. It
is not currently backed by operating-system authentication or institutional RBAC.
Do not expose the service on a LAN.

## Data And Privacy Gate

Only these classifications are permitted:

- `synthetic`: invented text containing no real participant information; or
- `authorized-deidentified`: a transcript already de-identified under an approved
  project process and explicitly authorized for the pinned remote provider path.

Before any transcript inference call, the researcher must provide all of the
following:

- the classification;
- an explicit remote-egress authorization;
- confirmation that the transcript contains no direct identifiers;
- a meaningful authorization or de-identification basis; and
- a researcher-authorized job cost greater than the worst-case preflight estimate
  and no greater than the local global ceiling.

A local pattern screen checks common email, telephone, government-ID, and IP-address
forms. It is defense in depth, not proof of de-identification. It will miss some
names, locations, rare identifiers, indirect identifiers, and contextual
re-identification risks. `authorized-deidentified` input fails closed when the
screen finds a possible direct identifier, but passing the screen does not create
authorization.

Source intake authorization is not silently reused as job authorization. Every job
requires the named researcher to confirm the exact active revision and the fixed
four-specialists-per-chunk egress plan. The store binds that confirmation, actor,
time, revision ID, and transcript SHA-256 to the job, and the exact revision is read
from the verified local text store and screened again before it can be queued.

All four specialists receive the same source chunk remotely through OpenRouter.
The redaction specialist runs after egress; its output cannot make the input safe
to send. The remote path is pinned to `openai/gpt-5.6-luna` on `azure/eu`, requires
ZDR and denied data collection, disables provider fallback, and disables cache.
It also disables the web, response-healing, and context-compression plugins and
rejects any reported router pipeline stage.
If the pinned route, ZDR declaration, schema parameters, key status, or price
preflight cannot be verified, the run stops visibly before inference.

The OpenRouter key stays backend-only. It must never appear in source, frontend
state, stored job artifacts, exports, screenshots, logs, or chat. Use a dedicated
limited-spend key and rotate it after any suspected exposure.

## Source, Chunk, And Call Contract

The authoritative input rules are in
[`docs/transcript-protocol-v1.md`](transcript-protocol-v1.md).

The pilot:

1. preserves the original source blob and its SHA-256;
2. creates an immutable original transcript revision;
3. canonicalizes the transcript under the versioned protocol;
4. splits the ordered non-empty lines into deterministic, non-overlapping chunks;
5. records every chunk boundary and chunk SHA-256 before execution;
6. records four pending specialist calls for each chunk before the first call; and
7. performs calls in chunk order and fixed specialist order.

Source intake first reserves stable source/import identities in a local journal.
If publication across the blob, text, catalog, and pilot stores is interrupted, an
identical retry replays those identities instead of creating duplicate lineage.

Version 1 chunks contain at most six lines and at most 2,400 UTF-8 bytes. There is
no context overlap. Local line indexes in a specialist response are translated
through the stored chunk start index; the final proposal set must cover every
global source line exactly once and in source order.

There is no retry, response healing, alternate provider, judge, or unplanned fifth
call within a chunk. A schema error, missing line, duplicate line, provider error,
or incomplete usage receipt fails the affected run. Partial results remain visible
for audit but never become a reviewable or accepted transcript revision.

## Cost And Usage Contract

Preflight determines the chunk count, planned call count, endpoint capabilities,
current request/prompt/completion prices, maximum prompt and completion bounds, and
conservative worst-case job cost before inference. A nonzero per-request price is
currently unsupported and fails closed. For each planned call, the authorization
bound is:

```text
B = request_price + (8,000 * prompt_price) + (800 * completion_price)
```

The 8,000 bound is a conservative ceiling paired with a serialized-request byte
check; it is not a claim about model context capacity. Inference begins only when:

```text
estimated_max_cost_usd < researcher_authorized_cost_usd <= 5.00
```

The same preflight prices are sent as the router's per-request `max_price`, so a
price increase after preflight is rejected before provider execution. Before every
call, the store atomically requires `known_cost + remaining_calls * B` to remain
below the authorization. Every returned receipt is checked against its own
append-only preflight attempt: completion is at most 800 tokens, reasoning is zero,
total tokens equal prompt plus completion, native cost is at most `B`, and
cumulative known cost remains within authorization. Distinct valid calls must also
have distinct provider generation IDs.

The receipt reports planned, attempted, completed, and valid calls separately.
Known provider charges remain visible even when accounting is incomplete. A total
cost is reported only when every planned call has a complete native provider
receipt and a valid strict result.

Cancellation does not erase attempted-call or cost evidence. A request already
accepted by the provider may finish and may be billed even after the researcher
asks to cancel.

## Human Review And Mutation Contract

Model output is always a proposal. Job completion alone never changes the active
transcript revision.

For every changed line, the owning researcher must choose exactly one current
decision:

- `accept`: use the locally composed candidate line;
- `keep_original`: retain the source line; or
- `edit`: use explicit researcher-authored text.

Unchanged lines require no decision. Decision history is append-only and
versioned; stale updates conflict rather than overwrite a newer choice. Researcher
edits are stored separately from immutable specialist results.

Commit is allowed only when every changed line has a decision and the source's
active revision still equals the revision on which the job was based. It compares
the exact versioned review-snapshot digest used to assemble the candidate and
requires at least one net line change. Only one publication can be in flight for a
source at a time. Commit creates a new `researcher-reviewed` child revision and
moves the active pointer to it. It never overwrites the original blob or original
revision. Restore moves the active pointer back to the original and records an
attributable audit event; it does not delete the generated revision, proposals,
decisions, or receipts. Restore is blocked while that source has an in-flight
publication.

## Idempotency, Cancellation, And Recovery

A source-scoped idempotency key identifies a logical job request. Repeating the
same key with the same request digest returns the existing job. Reusing the key
with different input, protocol, chunk plan, or cost authorization conflicts.

Cancellation is cooperative. It is checked before a new remote call. Pending calls
and unfinished chunks become `cancelled`; completed calls and their receipts remain
unchanged. A cancelled job cannot be reviewed or committed.

On local restart:

- interrupted `preflight` or `running` work with no in-flight call can return to
  the durable queue and continue only its still-pending plan, but its old preflight
  is retained as history, expired for new calls, and all three provider metadata
  checks must pass again first;
- a call stored as `calling` has an unknowable provider outcome and becomes
  `ambiguous`;
- any job containing an ambiguous call becomes `needs_attention` and is never
  automatically reissued; and
- an interrupted publish is replayed locally from its stored commit identity; it
  must reconcile the stored parent, revision, hash, import ID, job state, and active
  source before finalization, or remain visibly `needs_attention`.

The ambiguity rule is essential: after a crash between provider acceptance and
local receipt persistence, the application cannot prove whether the remote call
ran or was billed. Starting a replacement is a new explicit researcher decision,
not a hidden retry. Read timeouts, transport loss, HTTP 408, and provider-gateway
5xx responses are treated as ambiguous; definite router 4xx rejections are stored
as failed attempts instead.

Recovery also compares the job's stored provider, prompt, schema, merge, protocol,
and Git-commit contract to the current clean executable identity. A mismatch
becomes `needs_attention`; completed calls are never silently combined with a new
contract.

## Provenance And Stored Evidence

Each source, job, call, proposal, decision, and committed revision is linked by
stable identifiers and hashes. The job provenance record includes:

- the full Git commit from a clean checkout (dirty or unknown code identity is
  rejected before a job can queue transcript egress);
- product, transcript-protocol, prompt, schema, and merge versions;
- a protocol fingerprint and strict-schema digest;
- source blob, input transcript revision, request, and chunk digests;
- planned call count and researcher-authorized cost;
- exact-revision egress confirmation, authorization actor and time, revision ID,
  transcript SHA-256, and authorization digest;
- requested and returned model, provider, endpoint, ZDR preflight, generation ID,
  single-attempt routing metadata, empty router pipeline, finish reason, latency,
  tokens, cost, and accounting status when returned;
- call, chunk, cancellation, failure, and recovery states;
- original and proposed text lineage for each global line;
- researcher decision history and hashes of researcher edits; and
- parent and committed revision identifiers and transcript SHA-256.

The store must not persist the API key, authorization header, raw provider envelope,
or hidden reasoning. Audit metadata avoids transcript bodies; proposal and revision
content remains in the local pilot data store because it is required for review and
recovery.

Any professor walkthrough must distinguish a live run from a retained local run.
A saved receipt is historical evidence, not proof that a provider call just
occurred.

## One-Command Local Launch

Prerequisites are an existing `.venv` with the project installed and
`frontend/node_modules` installed. Configure the backend-only OpenRouter key in the
ignored local environment, then run from the repository root:

```bash
./scripts/run-transcript-pilot.sh
```

The launcher:

- binds both services to `127.0.0.1`;
- uses an isolated ignored data directory by default;
- exports `TRANSCRIPT_PILOT_CODE_COMMIT` and
  `TRANSCRIPT_PILOT_CODE_DIRTY` for job provenance;
- refuses to start from a dirty checkout or without a Git commit identity;
- waits for backend and frontend readiness;
- prints the local UI and log locations without printing a key; and
- terminates both child processes on exit, interrupt, or termination.

This launcher is a local pilot convenience, not a packaged installer. Ports and the
data directory may be overridden with `TRANSCRIPT_PILOT_BACKEND_PORT`,
`TRANSCRIPT_PILOT_FRONTEND_PORT`, and `NLP_SKILL_AGENTS_DATA_DIR`.

## Professor Walkthrough

Use only an owner-approved synthetic evaluation case or another clearly invented
transcript.

1. **Start locally.** Run `./scripts/run-transcript-pilot.sh` and open the printed
   `/transcript-pilot` URL. Point out the loopback addresses, clean commit identity,
   and isolated data path.
2. **State the boundary.** Explain that the UI and durable workflow are local but
   the transcript chunks are sent remotely to the pinned Luna endpoint. ZDR is a
   provider control, not authorization to send research data.
3. **Import synthetic text.** Show the classification and authorization fields.
   Explain that the local privacy screen is deliberately limited and that the
   redaction specialist is not a pre-egress safeguard.
4. **Preview the plan.** Show deterministic chunks, four specialists per chunk,
   `4 * chunk_count` planned calls, the authorized cost, and worst-case preflight.
5. **Run once.** Start the job. Show each strict result and native receipt. If the
   pinned endpoint or any result fails, stop; do not switch provider or present a
   mock result as live.
6. **Review changes.** Demonstrate one accepted proposal, one kept-original line,
   and one researcher edit when the fixture produces suitable changes. Point out
   that no active revision has changed yet.
7. **Commit deliberately.** Commit only after every changed line is resolved. Show
   the immutable parent, new child revision, reviewer attribution, transcript
   digest, and active pointer.
8. **Restore.** Restore the original. Show that the accepted child, decisions,
   specialist evidence, and receipts remain available.
9. **Close honestly.** Say: “This proves a bounded, source-linked, human-controlled
   revision workflow on synthetic input. It does not prove domain accuracy or
   authorize identifiable research data.”

## Walkthrough Stop Conditions

Do not continue the live demonstration when:

- the input is not clearly synthetic or explicitly authorized de-identified data;
- the privacy screen reports a possible identifier;
- the checkout provenance is unexpected and cannot be explained;
- current ZDR, provider pinning, strict-schema support, or cost cannot be verified;
- the planned call count differs from four times the chunk count;
- any call is ambiguous, missing a receipt, or schema-invalid; or
- any changed line remains unreviewed.
