package extractmethods

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"sort"
	"strings"
)

var (
	jwtPattern   = regexp.MustCompile(`(?m)([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})`)
	emailPattern = regexp.MustCompile(`(?i)^[^@\s]{1,128}@[^@\s]{1,190}\.[^@\s]{2,32}$`)
)

type Credential struct {
	AccessToken  string
	SessionToken string
	Email        string
	AccountID    string
	Label        string
	Hash         string
}

func ParseBatchCredentials(input string, rawItems json.RawMessage) ([]Credential, error) {
	var credentials []Credential
	if len(rawItems) > 0 && string(rawItems) != "null" {
		var value any
		decoder := json.NewDecoder(strings.NewReader(string(rawItems)))
		decoder.UseNumber()
		if err := decoder.Decode(&value); err != nil {
			return nil, errors.New("items 必须是有效的 JSON 数组或对象")
		}
		credentials = append(credentials, credentialsFromValue(value)...)
	}
	if strings.TrimSpace(input) != "" {
		credentials = append(credentials, credentialsFromText(input)...)
	}
	credentials = deduplicateCredentials(credentials)
	if len(credentials) == 0 {
		return nil, errors.New("没有识别到 Access Token；可粘贴 JSON 数组、逐行 JSON、纯 JWT 或包含 accessToken 的凭证")
	}
	if len(credentials) > DefaultMaxItems {
		return nil, errors.New("单批最多 500 个账号")
	}
	for index := range credentials {
		if credentials[index].Label == "" {
			if credentials[index].Email != "" {
				credentials[index].Label = credentials[index].Email
			} else {
				credentials[index].Label = "账号 " + stringValue(index+1)
			}
		}
	}
	return credentials, nil
}

func credentialsFromText(input string) []Credential {
	text := strings.TrimSpace(input)
	if text == "" {
		return nil
	}
	var parsed any
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	if decoder.Decode(&parsed) == nil {
		var trailing any
		if decoder.Decode(&trailing) == io.EOF {
			if items := credentialsFromValue(parsed); len(items) > 0 {
				return items
			}
		}
	}
	var result []Credential
	for _, rawLine := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			continue
		}
		var lineValue any
		lineDecoder := json.NewDecoder(strings.NewReader(line))
		lineDecoder.UseNumber()
		if lineDecoder.Decode(&lineValue) == nil {
			if items := credentialsFromValue(lineValue); len(items) > 0 {
				result = append(result, items...)
				continue
			}
		}
		if token := normalizeAccessToken(line); token != "" {
			credential := Credential{AccessToken: token, Email: emailFromJWT(token), AccountID: accountIDFromJWT(token)}
			credential.Hash = tokenHash(token)
			result = append(result, credential)
		}
	}
	return result
}

func credentialsFromValue(value any) []Credential {
	switch item := value.(type) {
	case []any:
		var result []Credential
		for _, child := range item {
			result = append(result, credentialsFromValue(child)...)
		}
		return result
	case map[string]any:
		for _, key := range []string{"items", "accounts", "credentials", "data", "results"} {
			if child, ok := lookupFold(item, key); ok {
				if array, ok := child.([]any); ok {
					var result []Credential
					for _, entry := range array {
						result = append(result, credentialsFromValue(entry)...)
					}
					if len(result) > 0 {
						return result
					}
				}
			}
		}
		access := findNestedString(item, []string{"accessToken", "access_token", "token", "access"})
		access = normalizeAccessToken(access)
		if access == "" {
			return nil
		}
		session := findNestedString(item, []string{"sessionToken", "session_token", "chatgptSessionCookie", "chatgpt_session_cookie", "__Secure-next-auth.session-token"})
		email := findNestedString(item, []string{"email", "mail", "username", "preferred_username"})
		if !emailPattern.MatchString(email) {
			email = emailFromJWT(access)
		}
		label := email
		if label == "" {
			label = findNestedString(item, []string{"label", "name", "account", "accountName"})
		}
		accountID := findNestedString(item, []string{"accountId", "account_id", "chatgpt_account_id"})
		if strings.TrimSpace(accountID) == "" {
			accountID = accountIDFromJWT(access)
		}
		return []Credential{{
			AccessToken: access, SessionToken: strings.TrimSpace(session), Email: email,
			AccountID: accountID, Label: label, Hash: tokenHash(access),
		}}
	case string:
		if token := normalizeAccessToken(item); token != "" {
			return []Credential{{AccessToken: token, Email: emailFromJWT(token), AccountID: accountIDFromJWT(token), Hash: tokenHash(token)}}
		}
	}
	return nil
}

func normalizeAccessToken(raw string) string {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ""
	}
	if strings.HasPrefix(strings.ToLower(text), "bearer ") {
		text = strings.TrimSpace(text[7:])
	}
	if strings.HasPrefix(text, "{") || strings.HasPrefix(text, "[") {
		var value any
		decoder := json.NewDecoder(strings.NewReader(text))
		decoder.UseNumber()
		if decoder.Decode(&value) == nil {
			items := credentialsFromValue(value)
			if len(items) > 0 {
				return items[0].AccessToken
			}
		}
	}
	if match := jwtPattern.FindStringSubmatch(text); len(match) > 1 {
		return match[1]
	}
	if strings.ContainsAny(text, " \t\r\n") {
		return ""
	}
	if len(text) >= 80 {
		return text
	}
	return ""
}

func findNestedString(value any, keys []string) string {
	wanted := make(map[string]bool, len(keys))
	for _, key := range keys {
		wanted[strings.ToLower(key)] = true
	}
	var walk func(any) string
	walk = func(node any) string {
		switch item := node.(type) {
		case map[string]any:
			ordered := make([]string, 0, len(item))
			for key := range item {
				ordered = append(ordered, key)
			}
			sort.Strings(ordered)
			for _, key := range ordered {
				if wanted[strings.ToLower(key)] {
					if value := scalarStringValue(item[key]); value != "" {
						return value
					}
				}
			}
			for _, key := range ordered {
				if value := walk(item[key]); value != "" {
					return value
				}
			}
		case []any:
			for _, child := range item {
				if value := walk(child); value != "" {
					return value
				}
			}
		}
		return ""
	}
	return walk(value)
}

func scalarStringValue(value any) string {
	switch value.(type) {
	case map[string]any, []any:
		return ""
	default:
		return stringValue(value)
	}
}

func lookupFold(values map[string]any, key string) (any, bool) {
	for current, value := range values {
		if strings.EqualFold(current, key) {
			return value, true
		}
	}
	return nil, false
}

func emailFromJWT(token string) string {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return ""
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return ""
	}
	var value map[string]any
	if json.Unmarshal(payload, &value) != nil {
		return ""
	}
	profile, _ := value["https://api.openai.com/profile"].(map[string]any)
	for _, candidate := range []string{
		stringValue(profile["email"]), stringValue(profile["email_address"]),
		stringValue(value["email"]), stringValue(value["preferred_username"]),
	} {
		candidate = strings.TrimSpace(candidate)
		if emailPattern.MatchString(candidate) {
			return candidate
		}
	}
	return ""
}

func planFromJWT(token string) string {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return ""
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return ""
	}
	var value map[string]any
	if json.Unmarshal(payload, &value) != nil {
		return ""
	}
	auth, _ := value["https://api.openai.com/auth"].(map[string]any)
	for _, candidate := range []string{
		stringValue(auth["chatgpt_plan_type"]), stringValue(auth["plan_type"]),
		stringValue(value["chatgpt_plan_type"]), stringValue(value["plan_type"]),
	} {
		if plan := normalizePlanName(candidate); plan != "" {
			return plan
		}
	}
	return ""
}

func accountIDFromJWT(token string) string {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return ""
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return ""
	}
	var value map[string]any
	if json.Unmarshal(payload, &value) != nil {
		return ""
	}
	auth, _ := value["https://api.openai.com/auth"].(map[string]any)
	for _, candidate := range []string{
		stringValue(auth["chatgpt_account_id"]), stringValue(auth["account_id"]),
		stringValue(value["chatgpt_account_id"]), stringValue(value["account_id"]),
	} {
		if candidate = strings.TrimSpace(candidate); candidate != "" {
			return candidate
		}
	}
	return ""
}

func tokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:8])
}

func deduplicateCredentials(input []Credential) []Credential {
	seen := map[string]bool{}
	result := make([]Credential, 0, len(input))
	for _, credential := range input {
		if credential.AccessToken == "" {
			continue
		}
		if credential.Hash == "" {
			credential.Hash = tokenHash(credential.AccessToken)
		}
		if seen[credential.Hash] {
			continue
		}
		seen[credential.Hash] = true
		result = append(result, credential)
	}
	return result
}

func MaskEmail(email string) string {
	value := strings.TrimSpace(email)
	parts := strings.SplitN(value, "@", 2)
	if len(parts) != 2 {
		return value
	}
	local := parts[0]
	if len(local) <= 2 {
		local = local[:1] + "*"
	} else {
		local = local[:2] + "***" + local[len(local)-1:]
	}
	return local + "@" + parts[1]
}
