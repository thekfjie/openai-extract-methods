# Version archive — 2026-08-06 Checkout compatibility

This release is archived in Git as two consecutive commits:

- **Pre-change snapshot:** `archive/pre-checkout-compatibility-20260806`
- **Current compatibility release:** `archive/checkout-compatibility-20260806`

The corresponding workspace verification bundle is kept at:

```text
artifacts/checkout-compatibility-notes-20260806/
```

It contains the baseline/modified snapshots, patch, rollback script, hashes, and verification record. Runtime credentials, job databases, raw HAR captures, generated binaries, and dependency trees remain excluded from Git.

Current release scope:

- Canonical country/currency pairs for direct Checkout.
- Separate OAICS final Checkout ID and nested Stripe payment-page ID.
- Stripe init uses only the observed `cs_live_`/`cs_test_` identifier.
- Backend compatibility observations remain in private job history and are removed from public API clones.
