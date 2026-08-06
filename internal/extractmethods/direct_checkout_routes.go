package extractmethods

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

const (
	DirectRouteAuto        = "auto"
	DirectRouteOAICSDirect = "oaics_direct"
	DirectRouteCSPrepared  = "cs_prepared"
)

var (
	errCardPrebindRequired = errors.New("CARD_PREBIND_REQUIRED: CS checkout requires a saved payment method before extraction")
	errCardContextMismatch = errors.New("SAVED_PAYMENT_METHOD_CONTEXT_MISMATCH: saved card is not attached to this Checkout customer")
)

type oaicsCheckoutFormContext struct {
	PublishableKey              string
	CustomerSessionClientSecret string
	Mode                        string
	Amount                      string
	Currency                    string
	PaymentMethodTypes          []string
	SetupFutureUsage            string
	ReturnURL                   string
}

type directPreparation struct {
	Route                 string
	Link                  string
	Amount                string
	Currency              string
	Methods               []string
	SavedPaymentMethodIDs []string
	FormContext           map[string]any
	LinkReady             bool
	FormReady             bool
	PreparedAt            string
	Err                   error
}

func normalizeDirectRoute(value string) string {
	return normalizeCheckoutRoute(value)
}

func selectDirectCheckoutRoute(checkout checkoutData, requested string) (string, error) {
	requested = normalizeDirectRoute(requested)
	hasOAICS := strings.TrimSpace(checkout.OpenAIID) != ""
	hasStripe := strings.TrimSpace(checkout.StripeID) != ""
	switch requested {
	case DirectRouteOAICSDirect:
		if !hasOAICS {
			return requested, errors.New("OAICS_DIRECT_UNAVAILABLE: checkout response did not include oaics_ identity")
		}
		return requested, nil
	case DirectRouteCSPrepared:
		if !hasStripe {
			return requested, errors.New("CS_PREPARED_UNAVAILABLE: checkout response did not include cs_live_/cs_test_ identity")
		}
		return requested, nil
	default:
		if hasOAICS {
			return DirectRouteOAICSDirect, nil
		}
		if hasStripe {
			return DirectRouteCSPrepared, nil
		}
		return DirectRouteAuto, errors.New("CHECKOUT_ROUTE_UNAVAILABLE: no supported Checkout identity was observed")
	}
}

func checkoutForDirectRoute(checkout checkoutData, route string) checkoutData {
	switch normalizeDirectRoute(route) {
	case DirectRouteOAICSDirect:
		if strings.TrimSpace(checkout.OpenAIID) != "" {
			checkout.ID = strings.TrimSpace(checkout.OpenAIID)
		}
	case DirectRouteCSPrepared:
		if strings.TrimSpace(checkout.StripeID) != "" {
			checkout.ID = strings.TrimSpace(checkout.StripeID)
		}
	}
	return checkout
}

// prepareDirectCheckout builds the hosted link and the corresponding payment
// form context behind one barrier. It creates exactly one Checkout: the two
// goroutines only prepare the two handoff artifacts for that same identity.
func (f *flow) prepareDirectCheckout(checkout checkoutData, route, observedAmount string, preboundPaymentMethods []string) directPreparation {
	result := directPreparation{Route: normalizeDirectRoute(route), Amount: observedAmount, Currency: checkout.Currency}
	linkCh := make(chan directPreparation, 1)
	formCh := make(chan directPreparation, 1)
	go func() {
		linkCh <- directPreparation{
			Link:      fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID),
			LinkReady: strings.TrimSpace(checkout.ID) != "" && strings.TrimSpace(checkout.ProcessorEntity) != "",
		}
	}()
	go func() {
		prepared := directPreparation{Route: normalizeDirectRoute(route)}
		switch prepared.Route {
		case DirectRouteOAICSDirect:
			context, err := f.resolveOAICSCheckoutFormContext(f.checkout, checkout)
			if err != nil {
				prepared.Err = err
				formCh <- prepared
				return
			}
			prepared.FormContext = map[string]any{
				"publishableKey": context.PublishableKey, "customerSessionClientSecret": context.CustomerSessionClientSecret,
				"mode": context.Mode, "amount": context.Amount, "currency": context.Currency,
				"paymentMethodTypes": append([]string(nil), context.PaymentMethodTypes...),
				"setupFutureUsage":   context.SetupFutureUsage, "returnUrl": context.ReturnURL,
			}
			prepared.Methods = append([]string(nil), context.PaymentMethodTypes...)
			prepared.Amount = context.Amount
			prepared.Currency = strings.ToUpper(context.Currency)
			prepared.FormReady = true
		case DirectRouteCSPrepared:
			saved := append([]string(nil), preboundPaymentMethods...)
			var err error
			if len(saved) == 0 {
				saved, err = f.savedPaymentMethodIDs(f.checkout)
			}
			if err != nil {
				prepared.Err = err
				formCh <- prepared
				return
			}
			prepared.SavedPaymentMethodIDs = saved
			if len(saved) == 0 {
				prepared.Err = errCardPrebindRequired
				formCh <- prepared
				return
			}
			_ = f.provider.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT)
			_ = f.provider.CopyCookiesFrom(f.promotion, f.engine.Endpoints.ChatGPT)
			init, err := f.stripeInit(f.provider, stripeInitCheckout(checkout), true)
			if err != nil {
				prepared.Err = err
				formCh <- prepared
				return
			}
			checkoutSaved := findPaymentMethodIDsDeep(init.Raw)
			if len(checkoutSaved) == 0 || !hasPaymentMethodIntersection(saved, checkoutSaved) {
				prepared.Err = errCardContextMismatch
				formCh <- prepared
				return
			}
			prepared.FormContext = map[string]any{
				"publishableKey": checkout.PublishableKey, "elementsSessionId": init.ElementsSessionID,
				"configId": init.ConfigID, "initChecksum": init.InitChecksum,
				"stripeJsId": init.StripeJSID, "paymentMethodTypes": append([]string(nil), init.Methods...),
				"savedPaymentMethodIds": append([]string(nil), checkoutSaved...),
			}
			prepared.Methods = append([]string(nil), init.Methods...)
			prepared.Amount = init.Amount
			prepared.Currency = strings.ToUpper(init.Currency)
			prepared.FormReady = true
		default:
			prepared.Err = errors.New("CHECKOUT_ROUTE_UNRESOLVED")
		}
		formCh <- prepared
	}()
	linkResult, formResult := <-linkCh, <-formCh
	result.Link, result.LinkReady = linkResult.Link, linkResult.LinkReady
	result.Err = formResult.Err
	result.FormContext, result.Methods = formResult.FormContext, formResult.Methods
	result.Amount = firstNonEmpty(result.Amount, formResult.Amount)
	result.Currency = strings.ToUpper(firstNonEmpty(formResult.Currency, result.Currency))
	result.SavedPaymentMethodIDs = formResult.SavedPaymentMethodIDs
	result.FormReady = formResult.FormReady
	if result.Err == nil && (!result.LinkReady || !result.FormReady) {
		result.Err = errors.New("CHECKOUT_PREPARATION_BARRIER_INCOMPLETE")
	}
	if result.Err == nil {
		result.PreparedAt = time.Now().UTC().Format(time.RFC3339)
	}
	return result
}

func applyDirectPreparationMetadata(metadata map[string]any, preparation directPreparation) {
	metadata["requiresPrebind"] = preparation.Route == DirectRouteCSPrepared
	metadata["routeDecisionSource"] = "observed_checkout_identity"
	metadata["linkReady"] = preparation.LinkReady
	metadata["formReady"] = preparation.FormReady
	metadata["prepared"] = preparation.Err == nil && preparation.LinkReady && preparation.FormReady
	metadata["preparedAt"] = preparation.PreparedAt
	metadata["savedPaymentMethodCount"] = len(preparation.SavedPaymentMethodIDs)
	if len(preparation.SavedPaymentMethodIDs) > 0 {
		metadata["savedPaymentMethodIds"] = append([]string(nil), preparation.SavedPaymentMethodIDs...)
	}
	if preparation.Route == DirectRouteCSPrepared {
		metadata["prebindStatus"] = map[bool]string{true: "ready", false: "required"}[len(preparation.SavedPaymentMethodIDs) > 0]
	} else {
		metadata["prebindStatus"] = "not_required"
	}
	if preparation.FormContext != nil {
		metadata["checkoutFormContext"] = preparation.FormContext
	}
	if preparation.Err != nil {
		metadata["preparationError"] = preparation.Err.Error()
	}
}

func (f *flow) resolveOAICSCheckoutFormContext(client *BrowserClient, checkout checkoutData) (oaicsCheckoutFormContext, error) {
	if !strings.HasPrefix(strings.ToLower(strings.TrimSpace(checkout.ID)), "oaics_") {
		return oaicsCheckoutFormContext{}, errors.New("OAICS_CONTEXT_INVALID_ID")
	}
	path := "/backend-api/payments/checkout/" + url.PathEscape(checkout.ProcessorEntity) + "/" + url.PathEscape(checkout.ID)
	response, err := client.Do(f.ctx, http.MethodGet, f.engine.Endpoints.ChatGPT+path, f.openAIHeaders(path, f.engine.Endpoints.ChatGPT+"/checkout/"+checkout.ProcessorEntity+"/"+checkout.ID), nil, true)
	if err != nil {
		return oaicsCheckoutFormContext{}, err
	}
	if response.Status >= 400 {
		return oaicsCheckoutFormContext{}, upstreamError("oaics.context", response)
	}
	var raw map[string]any
	if response.JSON(&raw) != nil {
		return oaicsCheckoutFormContext{}, errors.New("OAICS_CONTEXT_NON_JSON")
	}
	resolvedID := findStringDeep(raw, "checkout_session_id", "checkoutSessionId")
	pk := findStringDeep(raw, "publishable_key", "publishableKey")
	customerSecret := findStringDeep(raw, "customer_session_client_secret", "customerSessionClientSecret")
	methods := paymentMethodTypes(raw)
	if resolvedID != checkout.ID || !strings.HasPrefix(pk, "pk_") || !strings.HasPrefix(customerSecret, "cuss_secret_") || !containsFold(methods, "card") {
		return oaicsCheckoutFormContext{}, errors.New("OAICS_CONTEXT_INCOMPLETE")
	}
	return oaicsCheckoutFormContext{
		PublishableKey: pk, CustomerSessionClientSecret: customerSecret, Mode: "subscription",
		Amount: expectedAmount(raw), Currency: strings.ToUpper(firstNonEmpty(findStringDeep(raw, "currency"), checkout.Currency)),
		PaymentMethodTypes: methods, SetupFutureUsage: "off_session",
		ReturnURL: firstNonEmpty(findStringDeep(raw, "confirm_return_url", "return_url", "returnUrl"), f.engine.Endpoints.ChatGPT+"/checkout/"+checkout.ProcessorEntity+"/"+checkout.ID),
	}, nil
}

func (f *flow) savedPaymentMethodIDs(client *BrowserClient) ([]string, error) {
	path := "/backend-api/payments/payment_methods"
	target := f.engine.Endpoints.ChatGPT + path
	if accountID := strings.TrimSpace(f.credential.AccountID); accountID != "" {
		target += "?account_id=" + url.QueryEscape(accountID)
	}
	headers := f.openAIHeaders(path, f.engine.Endpoints.ChatGPT+"/")
	if accountID := strings.TrimSpace(f.credential.AccountID); accountID != "" {
		headers["ChatGPT-Account-Id"] = accountID
	}
	response, err := client.Do(f.ctx, http.MethodGet, target, headers, nil, true)
	if err != nil {
		return nil, err
	}
	if response.Status >= 400 {
		return nil, upstreamError("chatgpt.payment_methods", response)
	}
	var raw any
	if response.JSON(&raw) != nil {
		return nil, errors.New("PAYMENT_METHOD_QUERY_NON_JSON")
	}
	return savedPaymentMethodIDsFromResponse(raw), nil
}

func savedPaymentMethodIDsFromResponse(value any) []string {
	switch item := value.(type) {
	case []any:
		return paymentMethodIDsFromList(item)
	case map[string]any:
		for _, key := range []string{"data", "payment_methods", "paymentMethods"} {
			if list, ok := item[key].([]any); ok {
				return paymentMethodIDsFromList(list)
			}
		}
	}
	return nil
}

func paymentMethodIDsFromList(items []any) []string {
	result := make([]string, 0, len(items))
	seen := map[string]bool{}
	for _, value := range items {
		item, ok := value.(map[string]any)
		if !ok {
			continue
		}
		id := strings.TrimSpace(stringValue(item["id"]))
		if strings.HasPrefix(id, "pm_") && !seen[id] {
			seen[id] = true
			result = append(result, id)
		}
	}
	sort.Strings(result)
	return result
}

func findPaymentMethodIDsDeep(value any) []string {
	seen := map[string]bool{}
	var walk func(any)
	walk = func(node any) {
		switch item := node.(type) {
		case string:
			if strings.HasPrefix(strings.TrimSpace(item), "pm_") {
				seen[strings.TrimSpace(item)] = true
			}
		case map[string]any:
			for _, child := range item {
				walk(child)
			}
		case []any:
			for _, child := range item {
				walk(child)
			}
		}
	}
	walk(value)
	result := make([]string, 0, len(seen))
	for id := range seen {
		result = append(result, id)
	}
	sort.Strings(result)
	return result
}

func hasPaymentMethodIntersection(left, right []string) bool {
	seen := make(map[string]bool, len(left))
	for _, value := range left {
		seen[value] = true
	}
	for _, value := range right {
		if seen[value] {
			return true
		}
	}
	return false
}
