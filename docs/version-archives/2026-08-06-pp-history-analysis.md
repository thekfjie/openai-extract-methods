# PP/OAICS cohort audit archive — 2026-08-06

This archive contains a read-only, aggregate analysis of the local extraction/payment history.

- Window: 2026-08-05T00:00:00Z through 2026-08-06T16:51:25Z
- Jobs snapshot SHA-256: `2aa0968125748123c0810469c7a4ff2c13481f40f30a69c830c9acd01afab150`
- No live checkout, PayPal authorization, or payment mutation was executed by the audit.

## Key result

- Recent GB PayPal: `CS_LIVE` 14/251 (5.6%); `OAICS` 0/21 (0.0%); unclassified `NONE` 0/133.
- Earlier observed OAICS → GB PayPal joins: 0/57 across 6 identities.
- Exact `OAILIVE` occurrences: 0; it is kept separate from `CS_LIVE`.
- OAICS attempts have a shorter median terminal time (85.4 s) than CS_LIVE (257.4 s), but zero successes; this is early-failure behavior, not evidence of a faster successful route.

## Billing details verification

The Go direct-checkout normalizer canonicalizes country/currency pairs (`JP + PHP` becomes `JP + JPY`; proxy region does not rewrite billing country). PayPal country adaptation remains a separate path. Targeted Go tests and the PP history tests passed; see `artifacts/pp-history-analysis-20260806/verification.txt`.

## Artifacts

- `report.json` / `report.md`: full snapshot aggregate.
- `recent-cohort.json` / `recent-cohort.md`: recent-window family/cohort/duration tables.
- `verification.txt`: exact test commands, literal outputs, and exit statuses.

## Same-account card → PP follow-up

A second join uses the direct-card portal audit, pairs each OAICS checkout context to the nearest successful card-link event within three seconds, and then matches the normalized account email to later GB PayPal jobs.

- OAICS prior → GB PP: 9/15 first attempts succeeded (60.0%), median terminal duration 89.1 seconds.
- OAICS plus a later card failure → GB PP: 9/13 first attempts succeeded (69.2%).
- No preceding captured card link: 4/61 first attempts succeeded (6.6%), median terminal duration 206.3 seconds.
- All successful PayPal rows in the OAICS-prior cohort used `CS_LIVE`; OAICS was the preceding direct-card observation.
- Exact session continuity is not provable from old rows because the card audit did not persist the checkout session id alongside the account email.

Detailed files: `same-session-card-to-pp.json` and `same-session-card-to-pp.md`.
