package extractmethods

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"strings"
)

func prepareJobCountryOptions(method string, options Options, itemCount int) (Options, []Options, error) {
	mode := strings.ToLower(strings.TrimSpace(options.CountryMode))
	if mode == "" {
		mode = CountryModeSingle
	}

	switch mode {
	case CountryModeSingle:
		options.CountryMode = CountryModeSingle
		options.CountryPool = nil
		options.AssignmentStrategy = ""
		options.AssignmentSeed = ""
		options = normalizeOptions(method, options)
		applyCountryProxyOptions(&options)
		return options, repeatJobOptions(options, itemCount), nil
	case CountryModeRandom:
		return prepareRandomBalancedCountryOptions(method, options, itemCount)
	default:
		return Options{}, nil, fmt.Errorf("不支持的地区模式: %s；可用值为 single 或 random", strings.TrimSpace(options.CountryMode))
	}
}

func prepareRandomBalancedCountryOptions(method string, options Options, itemCount int) (Options, []Options, error) {
	if NormalizeMethod(method) != MethodPayPalBA {
		return Options{}, nil, fmt.Errorf("随机地区分配仅支持 PP 提炼（paypal_ba）")
	}

	strategy := strings.ToLower(strings.TrimSpace(options.AssignmentStrategy))
	if strategy == "" {
		strategy = AssignmentStrategyRandomBalanced
	}
	if strategy != AssignmentStrategyRandomBalanced {
		return Options{}, nil, fmt.Errorf("PP 随机地区不支持分配策略 %s；可用值为 random_balanced", strings.TrimSpace(options.AssignmentStrategy))
	}

	pool, err := normalizePayPalCountryPool(options.CountryPool)
	if err != nil {
		return Options{}, nil, err
	}
	if len(pool) < 2 {
		return Options{}, nil, fmt.Errorf("PP 随机地区至少需要选择 2 个不同的已适配国家")
	}

	seed := strings.TrimSpace(options.AssignmentSeed)
	if seed == "" {
		seed, err = newAssignmentSeed()
		if err != nil {
			return Options{}, nil, err
		}
	}

	options.CountryMode = CountryModeRandom
	options.CountryPool = append([]string(nil), pool...)
	options.AssignmentStrategy = AssignmentStrategyRandomBalanced
	options.AssignmentSeed = seed
	// Country and currency remain populated for older clients that still render
	// the singular fields. Per-account values below are authoritative.
	options.Country = pool[0]
	options.RequestedCountry = pool[0]
	options.Currency = currencyForCountry(pool[0])
	options.CountryFallback = false
	options = normalizeOptions(method, options)

	assignments := balancedCountryAssignments(pool, itemCount, seed)
	perItem := make([]Options, len(assignments))
	for index, country := range assignments {
		assigned := options
		assigned.CountryPool = append([]string(nil), pool...)
		assigned.Country = country
		assigned.RequestedCountry = country
		assigned.Currency = currencyForCountry(country)
		assigned.CountryFallback = false
		applyCountryProxyOptions(&assigned)
		perItem[index] = assigned
	}
	return options, perItem, nil
}

func applyCountryProxyOptions(options *Options) {
	if options == nil {
		return
	}
	country := strings.ToUpper(strings.TrimSpace(options.Country))
	if proxy := countryProxyValue(options.CountryProxies, country); proxy != "" {
		options.Proxy = proxy
	}
	if proxy := countryProxyValue(options.CountryPromotionProxies, country); proxy != "" {
		options.PromotionProxy = proxy
	}
}

func countryProxyValue(values map[string]string, country string) string {
	for key, value := range values {
		if strings.EqualFold(strings.TrimSpace(key), country) {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func normalizePayPalCountryPool(raw []string) ([]string, error) {
	pool := make([]string, 0, len(raw))
	seen := make(map[string]bool, len(raw))
	for _, value := range raw {
		country := normalizeRegion(value)
		if country == "" || !payPalCountryAdapted(country) {
			return nil, fmt.Errorf("PP 随机地区包含未适配国家: %s", strings.TrimSpace(value))
		}
		if country == "TR" {
			return nil, fmt.Errorf("TR 是 PP 优惠地区，不能放入随机主地区池")
		}
		if seen[country] {
			continue
		}
		seen[country] = true
		pool = append(pool, country)
	}
	return pool, nil
}

func repeatJobOptions(options Options, count int) []Options {
	if count < 0 {
		count = 0
	}
	result := make([]Options, count)
	for index := range result {
		result[index] = options
		result[index].CountryPool = append([]string(nil), options.CountryPool...)
	}
	return result
}

func balancedCountryAssignments(pool []string, count int, seed string) []string {
	if len(pool) == 0 || count < 1 {
		return nil
	}
	assignments := make([]string, 0, count)
	for round := uint64(0); len(assignments) < count; round++ {
		cycle := append([]string(nil), pool...)
		deterministicCountryShuffle(cycle, seed, round)
		remaining := count - len(assignments)
		if remaining < len(cycle) {
			cycle = cycle[:remaining]
		}
		assignments = append(assignments, cycle...)
	}
	return assignments
}

func deterministicCountryShuffle(values []string, seed string, round uint64) {
	for index := len(values) - 1; index > 0; index-- {
		var state [16]byte
		binary.BigEndian.PutUint64(state[:8], round)
		binary.BigEndian.PutUint64(state[8:], uint64(index))
		digestInput := make([]byte, 0, len(seed)+1+len(state))
		digestInput = append(digestInput, seed...)
		digestInput = append(digestInput, 0)
		digestInput = append(digestInput, state[:]...)
		digest := sha256.Sum256(digestInput)
		swapIndex := int(binary.BigEndian.Uint64(digest[:8]) % uint64(index+1))
		values[index], values[swapIndex] = values[swapIndex], values[index]
	}
}

func newAssignmentSeed() (string, error) {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "", fmt.Errorf("生成随机地区分配 seed 失败: %w", err)
	}
	return hex.EncodeToString(buffer), nil
}
