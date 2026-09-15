# Retained release evidence

These records support the completed LangChain refactor and customer-reply activation.
The [release summary](../release-validation.md) identifies the deployed source and remaining
maintenance. Checkpoint fields describe their own run; later release records supersede them.

| Record | Purpose |
| --- | --- |
| [Reviewed hosted acceptance](langchain-live-wave6-reviewed-20260915.json) | Final automatic and human gates, exact answers, usage, cost and latency; `rollout_ready=true` |
| [Human review submission](langchain-wave6-human-review-submitted-20260915.json) | Correctness and grounded-relevance scores for the exact 300 FAQ and nine focused answers |
| [Accounted hosted report](langchain-live-wave6-audit-accounted-20260915.json) | Report before importing the human review; supports offline reproduction |
| [Accounting provenance](langchain-live-wave6-audit-accounting-20260915.json) | Five conservative reserve corrections and source hashes |
| [Original hosted report](langchain-live-wave6-audit-20260915.json) | Source for the accounting correction |
| [Original hosted checkpoint](langchain-live-wave6-audit-20260915.checkpoint.json) | Per-turn usage and reservation evidence for that correction |
| [Outage acceptance](langchain-outage-wave6-audit-20260915.json) | Full deterministic recovery coverage without provider calls |
| [PostgreSQL burst](langchain-burst-wave6-audit-20260915.json) | Eight senders/four workers, durable reply queuing and duplicate checks |
| [Tool schema audit](langchain-tool-schema-wave6-audit-20260915.json) | Measured provider input boundary and native tool schemas |
| [Release preparation](langchain-wave7-local-20260915.json) | Local source fingerprints, migration, packaging, security and release checks |
| [Production deployment](langchain-production-wave8-20260915.json) | Exact images, staging/recovery, migration, backup references and full dark observation |
| [Customer activation](customer-sending-activation-20260915.json) | Follow-up source, explicit reply-window approval, actual runtime switches and counter continuity |

Intermediate diagnostics, rejection captures, superseded pre-LangChain reports and generated
review pages are excluded. Retain the accepted answers and the records required to trace their
review/accounting. Do not overwrite release evidence with a new run.

## Repeatable checks

Use the Python 3.11 environment and disposable PostgreSQL setup in the
[repository README](../../README.md#reproducible-checks-and-migrations). The evaluation suite
and scripts retain their `smart` names; they exercise the current LangChain implementation.
The [main matrix](../../data/evaluation/smart-non-escalation.tsv) and
[independent holdout](../../data/evaluation/smart-independent-holdout.tsv) remain reusable inputs.

Write diagnostics outside this evidence directory:

```bash
evaluation_dir=$(mktemp -d)
python scripts/release_eval.py --suite smart --mode outage \
  --output "$evaluation_dir/outage.json"
python scripts/smart_burst.py --output "$evaluation_dir/burst.json"
```

The burst requires `TEST_POSTGRES_URL` pointing to a migrated disposable PostgreSQL database.
Its timing ends when the reply is queued; Meta delivery is a separate channel check.

Reapply the saved human review without provider calls:

```bash
evaluation_dir=$(mktemp -d)
python scripts/release_eval.py --suite smart \
  --report docs/evaluation/langchain-live-wave6-audit-accounted-20260915.json \
  --reviews docs/evaluation/langchain-wave6-human-review-submitted-20260915.json \
  --max-cost 1 --output "$evaluation_dir/reviewed.json"
```

For a new hosted evaluation, use synthetic inputs and the authorized spend limit. Supply current
verified `--input-price`, `--output-price`, `--price-source`, `--price-checked` and `--max-cost`
values with `--mode live`. A subset is diagnostic evidence; full release acceptance requires
complete coverage and human review of that run's exact answers. Provider success alone is
not an answer-quality score.
