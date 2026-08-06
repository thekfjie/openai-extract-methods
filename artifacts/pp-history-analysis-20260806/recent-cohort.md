# Recent PP cohort audit (read-only)

- Snapshot SHA-256 (jobs): `2aa0968125748123c0810469c7a4ff2c13481f40f30a69c830c9acd01afab150`
- Window: 2026-08-05T00:00:00Z → 2026-08-06T16:51:25Z
- Recent rows: 668; PayPal rows: 533; GB PP: 405; US PP: 42

## Recent PP by family

| Scope | OAICS | CS_LIVE | NONE |
|---|---:|---:|---:|
| all | 0/29 (0.0%) | 20/323 (6.2%) | 0/181 (0.0%) |
| GB | 0/21 (0.0%) | 14/251 (5.6%) | 0/133 (0.0%) |
| US | 0/0 (—) | 0/31 (0.0%) | 0/11 (0.0%) |

## Duration (time to terminal result)

| Cohort | n | Median | P90 | Successes | Success median |
|---|---:|---:|---:|---:|---:|
| GB OAICS | 21 | 85.4s | 123.2s | 0 | — |
| GB CS_LIVE | 251 | 257.4s | 372.8s | 14 | 80.7s |
| GB NONE | 119 | 2.8s | 7.1s | 0 | — |

## Earlier family → GB PP (same identity, earlier timestamp)

| Cohort | Result | Identities |
|---|---:|---:|
| OAICS:direct_card | 0/0 (—) | 0 |
| OAICS:card_methods | 0/40 (0.0%) | 4 |
| OAICS:any_non_paypal | 0/57 (0.0%) | 6 |
| CS_LIVE:direct_card | 0/9 (0.0%) | 1 |
| CS_LIVE:card_methods | 0/9 (0.0%) | 1 |
| CS_LIVE:any_non_paypal | 0/180 (0.0%) | 27 |
| NONE:direct_card | 0/0 (—) | 0 |
| NONE:card_methods | 0/19 (0.0%) | 2 |
| NONE:any_non_paypal | 0/164 (0.0%) | 25 |

- Exact `OAILIVE` occurrences: 0

## Interpretation

- In this snapshot, all observed PayPal successes are `CS_LIVE`; `OAICS` has no PayPal successes.
- The earlier-OAICS→GB-PayPal cohorts also have 0 successes, so this sample does not support the claim that OAICS makes PayPal faster or more successful.
- OAICS attempts terminate sooner on median than CS_LIVE, but they also have 0 successes; this is an early-failure pattern, not a speed advantage.
- `NONE` means no recognized Checkout ID, not an OAILIVE hit.

## Limitations

- Checkout family is classified from observed IDs only; OAILIVE is not aliased to cs_live_.
- Prior cohorts require the same token hash or normalized email and an earlier timestamp.
- The join is descriptive and does not establish causation; direct-card audit events lack a family field.
- Duration is time-to-terminal-result; a shorter failed attempt is not evidence of a faster successful flow.
