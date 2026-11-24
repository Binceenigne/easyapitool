package main

import (
	_ "embed"
	"encoding/json"
	"strings"
)

// This is generated from the same reviewed registry used by the Python and
// Android Agents SDKs, rather than inferring limits from a model name prefix.
//
//go:generate node ../scripts/sync-agent-model-limits.mjs
//go:embed agent_model_limits.json
var agentModelLimitsJSON []byte

type agentModelLimit struct {
	ContextWindow    int64 `json:"context_window"`
	MaxInputTokens   int64 `json:"max_input_tokens"`
	MaxOutputTokens  int64 `json:"max_output_tokens"`
	ServerCompaction bool  `json:"server_compaction"`
}

type agentModelRegistry struct {
	Models          map[string]agentModelLimit `json:"models"`
	Aliases         map[string]string          `json:"aliases"`
	CompactionRatio float64                    `json:"compaction_ratio"`
}

var officialAgentModelRegistry = func() agentModelRegistry {
	var registry agentModelRegistry
	if err := json.Unmarshal(agentModelLimitsJSON, &registry); err != nil {
		panic("invalid embedded Agent model limit registry")
	}
	return registry
}()

func applyAgentModelPolicy(fields object, model string) {
	registry := officialAgentModelRegistry
	model = strings.ToLower(strings.TrimSpace(model))
	if canonical, ok := registry.Aliases[model]; ok {
		model = canonical
	}
	limits, ok := registry.Models[model]
	if !ok {
		return // An undocumented model keeps its provider's default limits.
	}
	fields["max_output_tokens"] = limits.MaxOutputTokens
	inputLimit := limits.MaxInputTokens
	if inputLimit <= 0 {
		inputLimit = limits.ContextWindow
	}
	usable := min(inputLimit, limits.ContextWindow-limits.MaxOutputTokens)
	if limits.ServerCompaction && usable > 0 && registry.CompactionRatio > 0 && registry.CompactionRatio < 1 {
		fields["context_management"] = []object{{"type": "compaction", "compact_threshold": usable * 9 / 10}}
	}
}
