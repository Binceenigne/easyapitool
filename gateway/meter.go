package main

import (
	"bytes"
	"encoding/json"
)

// usageObserver reads bounded metadata while ignoring image data and other values.
// It recognizes usage on the image envelope or inside a Responses event's response.
type usageObserver struct {
	sse, quoted, escaped bool
	depth, responseDepth int
	name                 string
	nameBuf              [128]byte
	nameLen              int
	field                string
	start                bool
	captured             []byte
	captureDepth         int
	usage, svgUsage      object
	model, outputFormat  string
	serviceTier          string
	withTools            bool
	readString           string
}

func (o *usageObserver) Write(p []byte) (int, error) {
	length := len(p)
	for len(p) > 0 {
		// Skip large uninteresting string spans (most response bytes are base64).
		if o.quoted && !o.escaped && o.nameLen > len(o.nameBuf) && o.captureDepth == 0 {
			index := bytes.IndexAny(p, "\"\\")
			if index < 0 {
				break
			}
			p = p[index:]
		}
		c := p[0]
		p = p[1:]
		if o.sse && c == '\n' {
			o.quoted, o.escaped, o.start = false, false, false
			o.depth, o.responseDepth, o.captureDepth = 0, 0, 0
			o.name, o.field = "", ""
			o.readString = ""
			o.captured = o.captured[:0]
			continue
		}
		if o.captureDepth != 0 {
			if len(o.captured) == 32<<10 {
				o.captureDepth = 0
				o.captured = o.captured[:0]
			} else {
				o.captured = append(o.captured, c)
			}
		}
		if o.quoted {
			switch {
			case o.escaped:
				o.escaped = false
			case c == '\\':
				o.escaped = true
				o.nameLen = len(o.nameBuf) + 1
			case c == '"':
				o.quoted = false
				if o.nameLen <= len(o.nameBuf) && (o.depth == 1 || o.depth == o.responseDepth) {
					o.name = string(o.nameBuf[:o.nameLen])
					switch o.readString {
					case "model":
						o.model = o.name
					case "output_format":
						o.outputFormat = o.name
					case "service_tier":
						o.serviceTier = o.name
					}
				}
				o.readString = ""
			default:
				if o.nameLen < len(o.nameBuf) {
					o.nameBuf[o.nameLen] = c
				}
				if o.nameLen <= len(o.nameBuf) {
					o.nameLen++
				}
			}
			continue
		}
		switch c {
		case ' ', '\t', '\r', '\n':
			continue
		case ':':
			o.start = oneOf(o.name, "usage", "svg_usage", "model", "output_format", "service_tier", "tools") || (o.depth == 1 && o.name == "response")
			continue
		case '"':
			if o.depth > 0 {
				o.quoted = true
				o.nameLen = 0
				if o.start && oneOf(o.name, "model", "output_format", "service_tier") {
					o.readString = o.name
				}
			}
		case '{', '[':
			o.depth++
			if c == '[' && o.start && o.name == "tools" {
				o.withTools = true
			}
			if c == '{' && o.start && oneOf(o.name, "usage", "svg_usage", "response") {
				if o.name == "response" {
					o.responseDepth = o.depth
				} else {
					o.captureDepth, o.field = o.depth, o.name
					o.captured = append(o.captured[:0], c)
				}
			}
		case '}', ']':
			if o.depth == o.captureDepth && o.captureDepth > 0 {
				var detail object
				if json.Unmarshal(o.captured, &detail) == nil {
					if o.field == "usage" {
						o.usage = detail
					} else {
						o.svgUsage = detail
					}
				}
				o.captureDepth = 0
				o.captured = o.captured[:0]
			}
			if o.depth == o.responseDepth {
				o.responseDepth = 0
			}
			o.depth = max(0, o.depth-1)
		}
		o.name, o.start = "", false
	}
	return length, nil
}

func (o *usageObserver) meter() *meter {
	return usageMeter(o.usage, o.svgUsage)
}

func usageMeter(usage, svgUsage object) *meter {
	input, i := tokenCount(usage["input_tokens"])
	output, o := tokenCount(usage["output_tokens"])
	total, t := tokenCount(usage["total_tokens"])
	if !t && (!i || !o) {
		return nil
	}
	if !t {
		total = input + output
	}
	return &meter{Input: input, Output: output, Total: total, Detail: object{"usage": usage, "svg_usage": svgUsage}}
}

func tokenCount(v any) (int64, bool) {
	num, ok := v.(float64)
	return int64(num), ok && num >= 0 && num <= 1e12 && num == float64(int64(num))
}
