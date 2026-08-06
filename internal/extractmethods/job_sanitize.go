package extractmethods

import "strings"

const invalidUPIMaterialDetail = "静态 UPI 图标或普通 Stripe Checkout 链接已过滤，未获得真实 UPI 支付材料"

func sanitizeLoadedJob(job *Job) bool {
	if job == nil {
		return false
	}
	changed := false
	for index := range job.Items {
		item := &job.Items[index]
		if !usableJobLabel(item.Label) {
			label := strings.TrimSpace(item.Email)
			if label == "" {
				label = "账号 " + stringValue(item.Index)
			}
			if item.Label != label {
				item.Label = label
				changed = true
			}
		}
		if NormalizeMethod(job.Method) == MethodUPI && sanitizeUPIJobItem(item) {
			changed = true
		}
	}
	return changed
}

func usableJobLabel(value string) bool {
	label := strings.TrimSpace(value)
	if label == "" {
		return false
	}
	lower := strings.ToLower(label)
	return !strings.HasPrefix(lower, "map[") &&
		!strings.HasPrefix(lower, "[object object]") &&
		!(strings.HasPrefix(label, "{") && strings.HasSuffix(label, "}"))
}

func sanitizeUPIJobItem(item *JobItem) bool {
	if item == nil {
		return false
	}
	var result Result
	if item.Result != nil {
		result = *item.Result
	}
	metadata := result.Metadata

	payload := firstNonEmpty(item.UPIPayload, result.UPIPayload, metadataString(metadata, "upiPayload", "upi_payload"))
	if !strings.HasPrefix(strings.ToLower(strings.TrimSpace(payload)), "upi://pay") {
		payload = ""
	}
	instruction := firstNonEmpty(item.UPIInstructionURL, result.UPIInstructionURL, metadataString(metadata, "instructionUrl", "instruction_url"))
	if !validUPIInstructionURL(instruction) {
		instruction = ""
	}
	png := firstNonEmpty(item.QRPNGURL, result.QRPNGURL, metadataString(metadata, "qrPngUrl", "qr_png_url"))
	if !validUPIQRImageURL("image_url_png", png, "png") {
		png = ""
	}
	svg := firstNonEmpty(item.QRSVGURL, result.QRSVGURL, metadataString(metadata, "qrSvgUrl", "qr_svg_url"))
	if !validUPIQRImageURL("image_url_svg", svg, "svg") {
		svg = ""
	}
	providerRedirect := firstNonEmpty(item.ProviderRedirectURL, result.ProviderRedirectURL)
	if !validUPIInstructionURL(providerRedirect) {
		providerRedirect = ""
	}
	stripeRedirect := firstNonEmpty(item.StripeRedirectURL, result.StripeRedirectURL)
	if !validUPIInstructionURL(stripeRedirect) {
		stripeRedirect = ""
	}
	legacyLongURL := firstNonEmpty(item.LongURL, result.LongURL)
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(legacyLongURL)), "upi://pay") && payload == "" {
		payload = legacyLongURL
	}
	if providerRedirect == "" && stripeRedirect == "" && validUPILegacyLongURL(legacyLongURL) {
		providerRedirect = legacyLongURL
	}
	longURL := firstNonEmpty(instruction, providerRedirect, stripeRedirect, png, svg, payload)

	changed := item.UPIPayload != payload || item.UPIInstructionURL != instruction || item.QRPNGURL != png ||
		item.QRSVGURL != svg || item.ProviderRedirectURL != providerRedirect || item.StripeRedirectURL != stripeRedirect || item.LongURL != longURL
	item.UPIPayload = payload
	item.UPIInstructionURL = instruction
	item.QRPNGURL = png
	item.QRSVGURL = svg
	item.ProviderRedirectURL = providerRedirect
	item.StripeRedirectURL = stripeRedirect
	item.LongURL = longURL

	if item.Result != nil {
		result.UPIPayload = payload
		result.UPIInstructionURL = instruction
		result.QRPNGURL = png
		result.QRSVGURL = svg
		result.ProviderRedirectURL = providerRedirect
		result.StripeRedirectURL = stripeRedirect
		result.LongURL = longURL
		if result.Metadata != nil {
			setMetadataString(result.Metadata, "upiPayload", payload)
			setMetadataString(result.Metadata, "instructionUrl", instruction)
			setMetadataString(result.Metadata, "qrPngUrl", png)
			setMetadataString(result.Metadata, "qrSvgUrl", svg)
			delete(result.Metadata, "upi_payload")
			delete(result.Metadata, "instruction_url")
			delete(result.Metadata, "qr_png_url")
			delete(result.Metadata, "qr_svg_url")
		}
	}

	hasMaterial := payload != "" || instruction != "" || png != "" || svg != "" || providerRedirect != "" || stripeRedirect != ""
	if !hasMaterial && (item.Status == ItemSucceeded || item.ExtractionStatus == "upi_ready") {
		item.Status = ItemFailed
		item.Stage = "upi.material.validation"
		item.Detail = invalidUPIMaterialDetail
		item.Error = invalidUPIMaterialDetail
		item.ExtractionStatus = "not_available"
		item.PaymentStatus = "not_started"
		item.Steps = invalidateUPIMaterialStep(item.Steps, item.FinishedAt)
		if item.Result != nil {
			result.OK = false
			result.ExtractionStatus = item.ExtractionStatus
			result.PaymentStatus = item.PaymentStatus
			result.Steps = append([]Step(nil), item.Steps...)
		}
		changed = true
	}
	if item.Result != nil {
		*item.Result = result
	}
	return changed
}

func invalidateUPIMaterialStep(steps []Step, at string) []Step {
	result := append([]Step(nil), steps...)
	for index := range result {
		if strings.EqualFold(result[index].Stage, "upi.material") {
			result[index].Status = "failed"
			result[index].Detail = invalidUPIMaterialDetail
			return result
		}
	}
	return append(result, Step{At: at, Stage: "upi.material.validation", Status: "failed", Detail: invalidUPIMaterialDetail})
}

func metadataString(metadata map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := metadata[key].(string); ok && strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func setMetadataString(metadata map[string]any, key, value string) {
	if value == "" {
		delete(metadata, key)
		return
	}
	metadata[key] = value
}
