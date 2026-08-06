package extractmethods

import "strings"

var privateCheckoutMetadataKeys = []string{
	"checkoutRoute", "requestedCountry", "requestedCurrency", "effectiveCountry",
	"effectiveCurrency", "billingConsistency", "oaicsObserved", "stripeObserved",
	"oaicsCheckoutId", "stripeCheckoutId", "observedCheckoutTypes",
}

// checkoutCompatibilityMetadata records observed Checkout families in the
// backend job history. It does not manufacture identifiers or expose a new UI
// switch; routing remains an observed/validated server-side decision.
func checkoutCompatibilityMetadata(checkout checkoutData, metadata map[string]any, requestedCountry, requestedCurrency, route string) map[string]any {
	if metadata == nil {
		metadata = map[string]any{}
	}
	requestedCountry = strings.ToUpper(strings.TrimSpace(requestedCountry))
	requestedCurrency = strings.ToUpper(strings.TrimSpace(requestedCurrency))
	effectiveCurrency := strings.ToUpper(strings.TrimSpace(checkout.Currency))
	if requestedCurrency == "" {
		requestedCurrency = effectiveCurrency
	}
	metadata["checkoutRoute"] = normalizeCheckoutRoute(route)
	metadata["requestedCountry"] = requestedCountry
	metadata["requestedCurrency"] = requestedCurrency
	metadata["effectiveCountry"] = strings.ToUpper(strings.TrimSpace(checkout.Country))
	metadata["effectiveCurrency"] = effectiveCurrency
	metadata["billingConsistency"] = "country_currency_enforced"
	metadata["oaicsObserved"] = strings.TrimSpace(checkout.OpenAIID) != ""
	metadata["stripeObserved"] = strings.TrimSpace(checkout.StripeID) != ""
	if strings.TrimSpace(checkout.OpenAIID) != "" {
		metadata["oaicsCheckoutId"] = strings.TrimSpace(checkout.OpenAIID)
	}
	if strings.TrimSpace(checkout.StripeID) != "" {
		metadata["stripeCheckoutId"] = strings.TrimSpace(checkout.StripeID)
	}
	return metadata
}

func normalizeCheckoutRoute(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "oaics", "oaics_preferred", "oaics-preferred":
		return "oaics_preferred"
	case "stripe", "cs", "cs_preferred", "cs-preferred":
		return "stripe_preferred"
	default:
		return "observed"
	}
}

// stripPrivateCheckoutMetadata removes compatibility diagnostics from API
// clones while leaving the manager's in-memory/persisted job state intact.
// This keeps routing observations available to backend history and audits
// without adding a frontend disclosure surface.
func stripPrivateCheckoutMetadata(metadata map[string]any) {
	for _, key := range privateCheckoutMetadataKeys {
		delete(metadata, key)
	}
}
