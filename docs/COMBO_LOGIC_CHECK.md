# Combo Logic Check (2026-07-25)

## Axes
- **Billing country** (`billingCountry` / `billing_country`):
  - drives ChatGPT checkout `billing_details.country`
  - drives currency via `currency_for_country`
  - drives billing profile / timezone seeds
- **Promo country** (`promotionProxy` region, e.g. `region-TR`):
  - drives `checkout/update` promo stage only
  - free-form: TR / VN / GB / BH / BR / TH / ...
  - not hard-forced to VN for PayPal
- **Provider proxy** (`providerProxy`):
  - Stripe lane only
  - independent from billing/promo countries
- **Checkout/Approve proxy**:
  - ChatGPT checkout + approve lane
  - approve reuses checkout proxy

## Operator meaning of `billing+promo`
Examples:
- `AE+TR` => billing AE/AED, promo TR
- `GB+TR` => billing GB/GBP, promo TR
- `GB+VN` => billing GB/GBP, promo VN
- `GB+GB` => billing GB/GBP, promo GB
- `US+BH` => billing US/USD, promo BH
- `BR+BR` => billing BR/BRL, promo BR
- `TH+TH` => billing TH/THB, promo TH
- `GB+BR` => billing GB/GBP, promo BR

These are not hardcoded product SKUs. Fill billing country + promotion proxy region; backend keeps them independent.

## Guards
- Accept any ISO-2 billing country (no silent rewrite to DE)
- Amount guard rejects full-price by default (`1933` blocked)
- Allows 0 / small residual (`<= OPENAI_PAY_MAX_PROMO_AMOUNT`, default 100)
- PayPal default UI mode: hosted
- Prefer `OPENAI_PAY_HTTP_CLIENT=requests` with proxies

## Validation result
Local mapping checks for the example matrix all passed.
No live BA spam was run in this logic pass.
