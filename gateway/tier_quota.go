package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

type tierDefaultQuota struct {
	ExpiresInDays int   `json:"expiresInDays"`
	Total         int64 `json:"totalMicroUSD"`
	H5            int64 `json:"fiveHourMicroUSD"`
	D1            int64 `json:"dayMicroUSD"`
	D7            int64 `json:"weekMicroUSD"`
}

// Missing defaults remain zero for existing tiers. Explicit values must be
// integer JSON numbers; null must not silently turn a limit into "unlimited".
func (q *tierDefaultQuota) UnmarshalJSON(data []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	if fields == nil {
		return errors.New("默认额度须为 JSON 对象")
	}
	for name, value := range fields {
		for _, field := range []string{"expiresInDays", "totalMicroUSD", "fiveHourMicroUSD", "dayMicroUSD", "weekMicroUSD"} {
			if strings.EqualFold(name, field) && bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
				return errors.New("默认有效期和额度须填写整数，0 表示不限")
			}
		}
	}
	type wire tierDefaultQuota
	var decoded wire
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	*q = tierDefaultQuota(decoded)
	return nil
}

func (q tierDefaultQuota) validate() error {
	if q.ExpiresInDays < 0 || q.ExpiresInDays > 36500 {
		return errors.New("默认有效期须在 0 至 36500 天之间，0 表示永久")
	}
	p := keyPolicy{Total: q.Total, H5: q.H5, D1: q.D1, D7: q.D7}
	return p.validate()
}

func (q tierDefaultQuota) apply(p keyPolicy, now time.Time) keyPolicy {
	p.Total, p.H5, p.D1, p.D7 = q.Total, q.H5, q.D1, q.D7
	p.ExpiresAt = nil
	if q.ExpiresInDays > 0 {
		expires := now.UTC().Add(time.Duration(q.ExpiresInDays) * 24 * time.Hour)
		p.ExpiresAt = &expires
	}
	return p
}

type tierDefaultsChoice bool

func (choice *tierDefaultsChoice) UnmarshalJSON(data []byte) error {
	switch string(bytes.TrimSpace(data)) {
	case "true":
		*choice = true
	case "false":
		*choice = false
	default:
		return errors.New("useTierDefaults 须为布尔值")
	}
	return nil
}
