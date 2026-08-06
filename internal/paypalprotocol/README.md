# PayPal Protocol Module

This directory owns the standalone PayPal agreement protocol workspace.

- `protocol/` contains the imported Python protocol skeleton and captured JSON references.
- The module is separate from `internal/extractmethods`; extraction produces links, while this module models the downstream agreement flow.
- The current AutoMyAI API exposes inventory/status only. No executor, OTP submission, browser verification, or final authorization route is registered.

The protocol core is country-parameterized. The imported skeleton currently has explicit locale/phone/address presets for BR, GB, US and TH; other country schemas must be added explicitly instead of silently falling back to Brazil data.
