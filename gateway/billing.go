package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/netip"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var (
	errKeyIP              = errors.New("当前 IP 不在该密钥的允许范围内")
	errPricingUnavailable = errors.New("该密钥的 USD 限额需要配置计价规则和预留金额")
)

type keyPolicy struct {
	IPs       []string   `json:"ips"`
	ExpiresAt *time.Time `json:"expiresAt"`
	Total     int64      `json:"totalMicroUSD"`
	H5        int64      `json:"fiveHourMicroUSD"`
	D1        int64      `json:"dayMicroUSD"`
	D7        int64      `json:"weekMicroUSD"`
}

func (p keyPolicy) encode() string {
	encoded, _ := json.Marshal(p)
	return string(encoded)
}

func (p keyPolicy) usdLimited() bool { return p.Total != 0 || p.H5 != 0 || p.D1 != 0 || p.D7 != 0 }

func (p keyPolicy) check(ip netip.Addr, now time.Time) error {
	if p.ExpiresAt != nil && !now.Before(*p.ExpiresAt) {
		return errInvalidKey
	}
	for _, prefix := range p.IPs {
		if subnet, err := netip.ParsePrefix(prefix); err == nil && subnet.Contains(ip) {
			return nil
		}
	}
	if len(p.IPs) != 0 {
		return errKeyIP
	}
	return nil
}

func (p *keyPolicy) validate() error {
	if len(p.IPs) > 64 {
		return errors.New("IP 白名单最多支持 64 项")
	}
	for i, val := range p.IPs {
		val = strings.TrimSpace(val)
		if addr, err := netip.ParseAddr(val); err == nil && addr.Zone() == "" {
			addr = addr.Unmap()
			p.IPs[i] = netip.PrefixFrom(addr, addr.BitLen()).String()
			continue
		}
		subnet, err := netip.ParsePrefix(val)
		if err != nil || subnet.Addr().Is4In6() {
			return errors.New("IP 白名单须填写 IPv4、IPv6 或 CIDR")
		}
		p.IPs[i] = subnet.Masked().String()
	}
	for _, amount := range []int64{p.Total, p.H5, p.D1, p.D7} {
		if amount < 0 || amount > 1_000_000_000_000 {
			return errors.New("USD 金额须在 0 至 100 万之间")
		}
	}
	return nil
}

type budgetRule struct {
	name  string
	span  time.Duration
	limit int64
}

func (p keyPolicy) budgets() []budgetRule {
	return []budgetRule{{"total", 0, p.Total}, {"5h", 5 * time.Hour, p.H5}, {"1d", 24 * time.Hour, p.D1}, {"7d", 168 * time.Hour, p.D7}}
}

func checkBudget(ctx context.Context, tx *sql.Tx, k accessKey, now time.Time, estimate int64) error {
	for _, rule := range k.Policy.budgets() {
		if rule.limit == 0 {
			continue
		}
		var used int64
		var oldest sql.NullInt64
		start := int64(0)
		if rule.span != 0 {
			start = now.Add(-rule.span).UnixMilli()
		}
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(SUM(COALESCE(cost_micro,reserve_micro)),0),
			MIN(CASE WHEN COALESCE(cost_micro,reserve_micro)>0 THEN created END) FROM requests WHERE key_id=? AND created>?`, k.ID, start).
			Scan(&used, &oldest); err != nil {
			return err
		}
		if used+estimate > rule.limit {
			var retry time.Time
			if rule.span != 0 && oldest.Valid && estimate <= rule.limit {
				retry = time.UnixMilli(oldest.Int64).Add(rule.span)
			}
			return &quotaError{resetAt: retry, currency: true}
		}
	}
	return nil
}

func budgetState(ctx context.Context, tx *sql.Tx, keyID string, rule budgetRule, now time.Time) (object, error) {
	start := int64(0)
	if rule.span != 0 {
		start = now.Add(-rule.span).UnixMilli()
	}
	var used int64
	var oldest sql.NullInt64
	err := tx.QueryRowContext(ctx, `SELECT COALESCE(SUM(COALESCE(cost_micro,reserve_micro)),0),
		MIN(CASE WHEN COALESCE(cost_micro,reserve_micro)>0 THEN created END) FROM requests WHERE key_id=? AND created>?`, keyID, start).
		Scan(&used, &oldest)
	const factor = 1_000_000.0
	item := object{"window": rule.name, "unit": "USD", "limit": float64(rule.limit) / factor, "used": float64(used) / factor}
	if rule.limit > 0 {
		item["remaining"] = float64(max(0, rule.limit-used)) / factor
	}
	if rule.span != 0 && oldest.Valid {
		windowStart := time.UnixMilli(oldest.Int64).UTC()
		item["window_start"], item["reset_at"] = windowStart, windowStart.Add(rule.span)
	}
	return item, err
}

// Values are millionths of a dollar per one million tokens.
type priceCard struct {
	TextInput, TextCached, ImageInput, ImageCached, Output int64
	Image                                                  bool
}

type priceInput struct {
	TextInput   string `json:"textInput"`
	TextCached  string `json:"textCached"`
	ImageInput  string `json:"imageInput"`
	ImageCached string `json:"imageCached"`
	Output      string `json:"output"`
	Image       bool   `json:"image"`
}

var moneyPattern = regexp.MustCompile(`^[0-9]{1,7}(\.[0-9]{1,6})?$`)

func parseMicroUSD(value string) (int64, error) {
	value = strings.TrimSpace(value)
	if !moneyPattern.MatchString(value) {
		return 0, errors.New("金额格式无效")
	}
	whole, fraction, _ := strings.Cut(value, ".")
	units, _ := strconv.ParseInt(whole, 10, 64)
	micro, _ := strconv.ParseInt(fraction+strings.Repeat("0", 6-len(fraction)), 10, 64)
	result := units*1_000_000 + micro
	if result > 1_000_000_000_000 {
		return 0, errors.New("金额超出范围")
	}
	return result, nil
}

func loadPricing(path string) (map[string]priceCard, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	inputs := map[string]priceInput{}
	if err := json.Unmarshal(data, &inputs); err != nil {
		return nil, fmt.Errorf("invalid pricing file: %w", err)
	}
	result := make(map[string]priceCard, len(inputs))
	for model, input := range inputs {
		card := priceCard{Image: input.Image}
		for _, value := range []struct {
			raw         string
			destination *int64
		}{
			{input.TextInput, &card.TextInput}, {input.TextCached, &card.TextCached}, {input.ImageInput, &card.ImageInput},
			{input.ImageCached, &card.ImageCached}, {input.Output, &card.Output},
		} {
			if value.raw == "" {
				*value.destination = -1
				continue
			}
			*value.destination, err = parseMicroUSD(value.raw)
			if err != nil {
				return nil, fmt.Errorf("invalid price for %s", model)
			}
		}
		if card.TextInput < 0 || card.Output < 0 || (card.Image && card.ImageInput < 0) {
			return nil, fmt.Errorf("missing required prices for %s", model)
		}
		result[model] = card
	}
	return result, nil
}

func quoteTokens(card priceCard, usage object) (*big.Int, bool) {
	input, hasInput := tokenCount(usage["input_tokens"])
	output, hasOutput := tokenCount(usage["output_tokens"])
	if !hasInput || !hasOutput {
		return nil, false
	}
	details := asObject(usage["input_tokens_details"])
	text, image := input, int64(0)
	cachedText, _ := tokenCount(details["cached_tokens"])
	cachedImage := int64(0)
	if card.Image {
		var hasText, hasImage bool
		text, hasText = tokenCount(details["text_tokens"])
		image, hasImage = tokenCount(details["image_tokens"])
		if !hasText || !hasImage || text+image != input {
			return nil, false
		}
		cacheDetails := asObject(details["cached_tokens_details"])
		if cachedText > 0 && cacheDetails == nil && image != 0 {
			return nil, false
		}
		if cacheDetails != nil {
			cachedText, _ = tokenCount(cacheDetails["text_tokens"])
			cachedImage, _ = tokenCount(cacheDetails["image_tokens"])
		}
	}
	if cachedText > text || cachedImage > image {
		return nil, false
	}
	sum := new(big.Int)
	for _, part := range [][2]int64{{text - cachedText, card.TextInput}, {cachedText, card.TextCached}, {image - cachedImage, card.ImageInput}, {cachedImage, card.ImageCached}, {output, card.Output}} {
		if part[0] == 0 {
			continue
		}
		if part[1] < 0 {
			return nil, false
		}
		sum.Add(sum, new(big.Int).Mul(big.NewInt(part[0]), big.NewInt(part[1])))
	}
	return sum, true
}

func (c config) price(model string, svg bool, count *meter) *int64 {
	if count == nil {
		return nil
	}
	card, ok := c.pricing[model]
	if !ok {
		return nil
	}
	value, priced := quoteTokens(card, asObject(count.Detail["usage"]))
	if !priced {
		return nil
	}
	if svg {
		detail := asObject(count.Detail["svg_usage"])
		author := objectString(detail, "model")
		if author == "" {
			return nil
		}
		card, ok := c.pricing[author]
		if !ok {
			return nil
		}
		extra, ok := quoteTokens(card, detail)
		if !ok {
			return nil
		}
		value.Add(value, extra)
	}
	value.Add(value, big.NewInt(999999)).Quo(value, big.NewInt(1000000))
	if !value.IsInt64() {
		return nil
	}
	cost := value.Int64()
	return &cost
}
