# Documentation

Start with the document that owns the decision you need. Release state is recorded separately
from application design and operating policy.

| Document | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Dialogue ownership, tools, state, authorization, model boundary and atomic recovery |
| [Requirements](requirements-summary.md) | Product scope, supported languages, ticket rules and acceptance targets |
| [Production operations](production-platform.md) | Deployment, monitoring, backup, compatible recovery and uncertain delivery |
| [Release status](release-validation.md) | Deployed source/images/settings, completed refactor, approvals and outstanding maintenance |
| [Beta contract](launch-contract.md) | Dates, traffic/budget limits, owners and stop conditions |
| [Application security](application-security.md) | Authentication, authorization and application controls |
| [Security launch checklist](security-launch-checklist.md) | Security gates for a release |
| [Secure media](secure-media.md) | Accepted uploads, scanning, private storage and access |
| [Privacy lifecycle](privacy-data-lifecycle.md) | Data inventory, deletion, holds, backups and hosted-provider boundaries |
| [Privacy addendum](privacy-notice-chatbot-addendum.md) | Website publication text and remaining owner decisions |
| [Knowledge corpus](knowledge-corpus.md) | Approved EN/MS/ZH seed content and CCO approval provenance |
| [Customer copy](customer-copy.md) | Approved customer wording and support targets |
| [Evaluation evidence](evaluation/README.md) | Final accepted reports, review provenance and repeatable evaluation commands |

Local setup, APIs and runnable checks are in the [repository README](../README.md).
The eight-wave dialogue refactor is complete; its durable decisions and closure evidence are
in architecture and release status. Keep future execution plans out of those reference docs.

For later releases, retain final acceptance and deployment evidence plus the inputs needed to
verify it. Run intermediate diagnostics into a temporary output directory. Update the current
release record when promotion completes, and remove superseded iteration reports after the
accepted result and its provenance are preserved. Keep approved content and reusable evaluation
matrices independently of old generated results.
