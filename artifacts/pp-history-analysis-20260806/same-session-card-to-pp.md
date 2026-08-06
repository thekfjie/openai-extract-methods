# Same-account direct-card → GB PayPal comparison (read-only)

- Jobs snapshot SHA-256: `2aa0968125748123c0810469c7a4ff2c13481f40f30a69c830c9acd01afab150`
- Join: OAICS context paired to a successful card-link audit event within 3 seconds, then normalized email matched to later GB PayPal rows.

## Linkage coverage

- OAICS context rows paired: 52; unique account emails: 18
- Successful card-link events without a paired OAICS context: 12; unique emails: 5

## Cohort comparison

| Cohort | Accounts | PP attempts | First-attempt successes | Rate | Eventual successful accounts | First median | Success-family |
|---|---:|---:|---:|---:|---:|---:|---|
| OAICS prior → GB PP | 15 | 44 | 9 | 60.0% | 9 | 89.1s | {'CS_LIVE': 9} |
| OAICS + later card failure → GB PP | 13 | 34 | 9 | 69.2% | 9 | 89.1s | {'CS_LIVE': 9} |
| Unmatched card link prior → GB PP | 1 | 5 | 0 | 0.0% | 0 | 3.8s | {} |
| No preceding captured card link | 61 | 389 | 4 | 6.6% | 5 | 206.3s | {'CS_LIVE': 5} |

## Same-account interpretation

- In this snapshot, the OAICS-prior cohort is strongly associated with later GB PayPal success: 9/15 first attempts succeeded (60.0%), versus 4/61 (6.6%) for accounts without a preceding captured card link.
- The OAICS observation is not the PayPal checkout identity: all successful rows in that cohort used `CS_LIVE` on the PayPal side.
- For the stricter OAICS + later card-failure subset, 9/13 first attempts succeeded (69.2%). This is still an observational, selected sample, not proof that OAICS causes the improvement.
- The only account with PP attempts both before and after the OAICS event had no success on either side; the within-account before/after sample is too small.

## Limitations

- Card audit does not persist checkout family per event; OAICS is inferred by pairing a context row to a successful card-link event within 3 seconds.
- The join uses normalized email, not a persistent session id; exact session continuity cannot be proven from current history.
- Shorter duration for failed attempts is not evidence of faster successful payment.
- No live payment or checkout mutation was executed by this audit.
