# Version archive — 2026-08-06 Direct Card dual route

Git archive tag:

```text
archive/direct-card-dual-route-20260806
```

Release scope:

- **OAICS direct** (`oaics_direct`): a single OAICS Checkout is created, then its hosted link and private payment-form context are prepared together. No saved-card prerequisite is introduced.
- **CS prepared** (`cs_prepared`): the backend requires an existing saved PaymentMethod before explicit CS extraction, initializes only the observed `cs_live_` / `cs_test_` payment page, and verifies that the saved PaymentMethod is present in the current Checkout customer context.
- **Auto policy**: routing is selected from the Checkout identity actually returned by the upstream response, never from the proxy region. OAICS is preferred when both identities are observed.
- Billing country/currency remains independent from the proxy exit. The Checkout billing pair is canonicalized internally; for example, PH/PHP remains PH/PHP even when the proxy exit is JP.
- The OAICS customer-session secret, CS Elements context, route diagnostics, saved PaymentMethod IDs, and preparation state remain private backend metadata and are removed from public job clones.
- The preparation barrier marks a result ready only when both the hosted link and its corresponding form context are ready.

Verification and rollback bundle:

```text
artifacts/direct-card-dual-route-20260806/
```

The bundle contains the modified source snapshot, source patch, hash manifests, exact verification record, and executable rollback script.
