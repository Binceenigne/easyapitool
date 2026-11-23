package main

import (
	"errors"
	"regexp"
	"slices"
)

// Both the native API proxy and the application adapter enforce documented
// model constraints. A broad tier allowlist cannot add an upstream capability.
// Auto remains valid: the upstream chooses within that model's own range.
func validateImageModelOptions(o imageOptions) error {
	if features, known := documentedImageFeatures(o.model); known {
		if features.SizeMode == "fixed" && !oneOf(o.size, "auto", "1024x1024", "1536x1024", "1024x1536") {
			return errors.New("所选模型仅支持标准尺寸")
		}
		if o.quality != "auto" && !slices.Contains(features.Quality, o.quality) {
			return errors.New("所选模型不支持该质量档位")
		}
		if o.complexity != "" && !features.Complexity {
			return errors.New("所选模型不支持复杂度")
		}
		if (o.format == "gif" && !features.Animation) || (o.format == "svg" && !features.SVG) {
			return errors.New("所选模型不支持该输出格式")
		}
	}
	return nil
}

type reasoningCapability struct {
	Efforts []string `json:"efforts"`
	Default string   `json:"default"`
}

var modelSnapshot = regexp.MustCompile(`-[0-9]{4}-[0-9]{2}-[0-9]{2}$`)

// https://developers.openai.com/api/docs/guides/image-generation
func documentedImageFeatures(model string) (imageFeatures, bool) {
	f := imageFeatures{Quality: []string{"low", "medium", "high"}, SizeMode: "flexible"}
	switch modelSnapshot.ReplaceAllString(model, "") {
	case "gpt-image-2.6-sunburst", "gpt-image-2.6-flare":
		f.Complexity, f.Animation, f.SVG = true, true, true
		f.Quality = append(f.Quality, "xhigh", "max")
	case "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2.5":
		f.Quality = append(f.Quality, "xhigh", "max")
	case "gpt-image-2":
	case "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini", "chatgpt-image-latest":
		f.SizeMode = "fixed"
	default:
		return imageFeatures{Quality: []string{}, SizeMode: "auto"}, false
	}
	return f, true
}

// Use model-specific effort lists from https://developers.openai.com/api/docs/models.
func documentedReasoning(model string) (reasoningCapability, bool) {
	switch modelSnapshot.ReplaceAllString(model, "") {
	case "gpt-6-luna", "gpt-6.1-terra", "gpt-6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol":
		return reasoningCapability{[]string{"none", "low", "medium", "high", "xhigh", "max"}, "medium"}, true
	case "gpt-6-astra", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-6.2-sol":
		return reasoningCapability{[]string{"low", "medium", "high", "xhigh", "max"}, "medium"}, true
	case "gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano":
		return reasoningCapability{[]string{"none", "low", "medium", "high", "xhigh"}, "none"}, true
	case "gpt-5.1":
		return reasoningCapability{[]string{"none", "low", "medium", "high"}, "none"}, true
	case "gpt-5.5":
		return reasoningCapability{[]string{"none", "low", "medium", "high", "xhigh"}, "medium"}, true
	case "gpt-5.3-codex":
		return reasoningCapability{[]string{"low", "medium", "high", "xhigh"}, "medium"}, true
	case "gpt-5.2-pro", "gpt-5.4-pro":
		return reasoningCapability{[]string{"medium", "high", "xhigh"}, "medium"}, true
	case "gpt-5.5-pro":
		return reasoningCapability{[]string{"medium", "high", "xhigh"}, "high"}, true
	case "gpt-5-pro":
		return reasoningCapability{[]string{"high"}, "high"}, true
	case "gpt-5", "gpt-5-mini", "gpt-5-nano":
		return reasoningCapability{[]string{"minimal", "low", "medium", "high"}, "medium"}, true
	case "o3", "o3-mini", "o4-mini":
		return reasoningCapability{[]string{"low", "medium", "high"}, "medium"}, true
	default:
		return reasoningCapability{}, false
	}
}

func supportedReasoningEffort(model, effort string) string {
	if caps, known := documentedReasoning(model); known && effort != "" && !slices.Contains(caps.Efforts, effort) {
		return caps.Default
	}
	return effort
}
