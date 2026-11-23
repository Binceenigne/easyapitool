package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"maps"
	"net/http"
	"slices"
	"sort"
	"strings"
	"time"
)

type modelPreset struct {
	Model  string `json:"model"`
	Effort string `json:"effort"`
}

// An omitted effort keeps the upstream's model default. Authorization and the
// client profile must use that same effective value without changing the saved
// administrator setting.
func effectiveReasoningEffort(model, effort string) string {
	if effort == "" {
		if capability, known := documentedReasoning(model); known {
			return capability.Default
		}
	}
	return effort
}

type svgSettings struct {
	Enabled bool `json:"enabled"`
	modelPreset
}

type imageFeatures struct {
	Complexity bool     `json:"complexity"`
	Animation  bool     `json:"animation"`
	SVG        bool     `json:"svg"`
	Quality    []string `json:"quality"`
	SizeMode   string   `json:"sizeMode"`
}

type modelSettings struct {
	Tiers                 []featureTier                  `json:"tiers,omitempty"`
	Tier                  *featureTier                   `json:"tier,omitempty"`
	Polish                modelPreset                    `json:"polish"`
	Continuation          modelPreset                    `json:"continuation"`
	Revision              int64                          `json:"revision"`
	DefaultImage          string                         `json:"defaultImage"`
	ImageModels           []string                       `json:"imageModels"`
	ImageFeatures         map[string]imageFeatures       `json:"imageFeatures"`
	SVG                   svgSettings                    `json:"svg"`
	Reasoning             map[string]modelPreset         `json:"reasoning"`
	ReasoningModes        map[string]modelPreset         `json:"reasoningModes"`
	ReasoningCapabilities map[string]reasoningCapability `json:"reasoningCapabilities"`
}

var builtinReasoningModels = map[string]string{
	"luna": "gpt-6-luna", "terra": "gpt-6.1-terra", "sol": "gpt-6.1-sol",
}

var builtinReasoningDepth = map[string]string{"luna": "medium", "terra": "xhigh", "sol": "xhigh"}

var normalReasoningSlots = map[string]string{
	"instant": "", "flash": "luna", "medium": "luna", "high": "terra", "extra": "terra", "max": "sol",
}

func defaultReasoningModes(slots map[string]modelPreset) map[string]modelPreset {
	modes := make(map[string]modelPreset, len(normalReasoningSlots))
	for mode, slot := range normalReasoningSlots {
		modes[mode] = slots[slot]
	}
	modes["instant"] = modelPreset{Effort: "none"}
	return modes
}

func modelDefaults(cfg config) modelSettings {
	images := []string{"gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2.6-flare", "gpt-image-2.6-sunburst"}
	if !slices.Contains(images, cfg.imageModel) {
		images = append([]string{cfg.imageModel}, images...)
	}
	settings := modelSettings{
		DefaultImage:  cfg.imageModel,
		ImageModels:   images,
		ImageFeatures: map[string]imageFeatures{},
		SVG:           svgSettings{Enabled: cfg.svgEnabled, modelPreset: modelPreset{Model: cfg.svgAuthor, Effort: cfg.svgEffort}},
		Reasoning:     make(map[string]modelPreset, len(builtinReasoningModels)),
	}
	for slot, fallback := range builtinReasoningModels {
		model := cfg.reasoningPresets[slot]
		if model == "" {
			model = cfg.reasoningModel
		}
		if model == "" {
			model = fallback
		}
		effort := cfg.reasoningEffort
		if effort == "" {
			effort = builtinReasoningDepth[slot]
		}
		settings.Reasoning[slot] = modelPreset{Model: model, Effort: effort}
	}
	settings.ReasoningModes = defaultReasoningModes(settings.Reasoning)
	settings.ensureTiers()
	settings.applyCapabilities()
	return settings
}

func (s *modelSettings) applyCapabilities() {
	s.ImageFeatures = make(map[string]imageFeatures, len(s.ImageModels))
	for _, model := range s.ImageModels {
		s.ImageFeatures[model], _ = documentedImageFeatures(model)
	}
	s.ReasoningCapabilities = make(map[string]reasoningCapability)
	collect := func(p modelPreset) modelPreset {
		if capability, known := documentedReasoning(p.Model); known {
			s.ReasoningCapabilities[p.Model] = capability
			p.Effort = supportedReasoningEffort(p.Model, p.Effort)
		}
		return p
	}
	s.Reasoning = maps.Clone(s.Reasoning)
	s.ReasoningModes = maps.Clone(s.ReasoningModes)
	for slot, preset := range s.Reasoning {
		s.Reasoning[slot] = collect(preset)
	}
	for mode, preset := range s.ReasoningModes {
		s.ReasoningModes[mode] = collect(preset)
	}
	s.SVG.modelPreset = collect(s.SVG.modelPreset)
}

func (s modelSettings) validate() error {
	if len(s.ImageModels) == 0 || len(s.ImageModels) > 128 || !slices.Contains(s.ImageModels, s.DefaultImage) {
		return errors.New("请选择至少一个生图模型，并从可选列表中设置默认模型")
	}
	seen := make(map[string]bool, len(s.ImageModels))
	for _, model := range s.ImageModels {
		if !modelPattern.MatchString(model) || seen[model] {
			return errors.New("生图模型 ID 无效或重复")
		}
		seen[model] = true
	}
	if !validModelPreset(s.SVG.modelPreset, true) {
		return errors.New("SVG 模型或思考深度无效")
	}
	if len(s.Reasoning) != len(builtinReasoningModels) {
		return errors.New("请配置 Luna、Terra 和 Sol 三档思维模型")
	}
	for name := range builtinReasoningModels {
		if preset := s.Reasoning[name]; !validModelPreset(preset, false) {
			return errors.New("请选择有效的思维模型和思考深度")
		}
	}
	if len(s.ReasoningModes) != len(normalReasoningSlots) {
		return errors.New("请配置 Instant、Flash、Medium、High、Extra 和 Max 六档预设")
	}
	for mode := range normalReasoningSlots {
		preset := s.ReasoningModes[mode]
		if _, exists := s.ReasoningModes[mode]; !exists || !validModelPreset(preset, mode == "instant") {
			return errors.New("请为 " + mode + " 选择有效的模型和思考深度")
		}
	}
	return nil
}

func validModelPreset(p modelPreset, allowDefaultModel bool) bool {
	return (modelPattern.MatchString(p.Model) || (allowDefaultModel && p.Model == "")) &&
		oneOf(p.Effort, "", "none", "minimal", "low", "medium", "high", "xhigh", "max")
}

func (g *gateway) loadModels(ctx context.Context) error {
	var data []byte
	err := g.store.db.QueryRowContext(ctx, "SELECT value FROM gateway_settings WHERE name='models'").Scan(&data)
	var settings modelSettings
	switch {
	case err == nil:
		if json.Unmarshal(data, &settings) != nil {
			return errors.New("已保存的模型配置无效")
		}
		if settings.ReasoningModes == nil {
			settings.ReasoningModes = defaultReasoningModes(settings.Reasoning)
		}
		settings.ensureTiers()
		if settings.validate() != nil {
			return errors.New("已保存的模型配置无效")
		}
		settings.applyCapabilities()
	case errors.Is(err, sql.ErrNoRows):
		settings = modelDefaults(g.cfg)
	default:
		return err
	}
	g.modelProfile.Store(&settings)
	return nil
}

func (g *gateway) appModels() *modelSettings {
	if current := g.modelProfile.Load(); current != nil {
		return current
	}
	defaults := modelDefaults(g.cfg)
	return &defaults
}

func (g *gateway) saveModels(ctx context.Context, proposed modelSettings) (*modelSettings, error) {
	g.modelMu.Lock()
	defer g.modelMu.Unlock()
	current := g.appModels()
	if proposed.Revision != current.Revision {
		return nil, errModelsChanged
	}
	if proposed.ReasoningModes == nil {
		proposed.ReasoningModes = current.ReasoningModes
	}
	if proposed.Tiers == nil {
		proposed.Tiers = current.Tiers
	}
	proposed.ensureTiers()
	if err := validateTiers(proposed.Tiers); err != nil {
		return nil, err
	}
	if err := g.validateTierAssignments(ctx, &proposed); err != nil {
		return nil, err
	}
	proposed.Tiers = sortedTiers(proposed.Tiers)
	proposed.Tier = nil
	proposed.Polish, proposed.Continuation = modelPreset{}, modelPreset{}
	if err := proposed.validate(); err != nil {
		return nil, err
	}
	proposed.applyCapabilities()
	proposed.Revision++
	data, err := json.Marshal(proposed)
	if err != nil {
		return nil, err
	}
	_, err = g.store.db.ExecContext(ctx, `INSERT INTO gateway_settings(name,value) VALUES ('models',?)
		ON CONFLICT(name) DO UPDATE SET value=excluded.value`, data)
	if err != nil {
		return nil, errModelsStorage
	}
	g.modelProfile.Store(&proposed)
	if g.onModelsChange != nil {
		g.onModelsChange(proposed.Revision)
	}
	return &proposed, nil
}

var errModelsChanged = errors.New("模型配置已更新，请刷新页面后重试")
var errModelsStorage = errors.New("模型配置暂时无法保存")

func (a *admin) configureModels(w http.ResponseWriter, r *http.Request) {
	var input modelSettings
	if !decodeRequest(w, r, 256<<10, &input) {
		return
	}
	saved, err := a.gateway.saveModels(r.Context(), input)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, errModelsChanged) {
			status = http.StatusConflict
		} else if errors.Is(err, errModelsStorage) {
			status = http.StatusServiceUnavailable
		}
		jsonError(w, status, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, saved)
}

func modelIDs(payload object) []string {
	items, _ := payload["data"].([]any)
	unique := make(map[string]bool, min(len(items), 1000))
	ids := make([]string, 0, min(len(items), 1000))
	for _, item := range items {
		id, ok := item.(string)
		if !ok {
			id = objectString(asObject(item), "id")
		}
		if modelPattern.MatchString(id) && !unique[id] {
			unique[id] = true
			ids = append(ids, id)
		}
		if len(ids) == 1000 {
			break
		}
	}
	sort.Strings(ids)
	return ids
}

func (a *admin) discoverModels(w http.ResponseWriter, r *http.Request) {
	u, err := a.gateway.store.getUpstream(r.Context(), r.PathValue("id"))
	if err != nil {
		jsonError(w, http.StatusNotFound, "上游账户不存在")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 12*time.Second)
	defer cancel()
	payload, err := a.gateway.getUpstreamJSON(ctx, u, "/models", "")
	if err != nil {
		jsonError(w, http.StatusBadGateway, err.Error())
		return
	}
	if _, ok := payload["data"].([]any); !ok {
		jsonError(w, http.StatusBadGateway, "上游未返回有效的模型列表")
		return
	}
	ids := modelIDs(payload)
	images := make([]string, 0)
	reasoning := make(map[string]reasoningCapability)
	for _, model := range ids {
		if _, known := documentedImageFeatures(model); known {
			images = append(images, model)
		}
		if capability, known := documentedReasoning(model); known {
			reasoning[model] = capability
		}
	}
	writeJSON(w, http.StatusOK, object{"upstreamId": u.ID, "models": ids, "imageModels": images, "reasoningCapabilities": reasoning})
}

func (g *gateway) managedKey(key keyInput) bool {
	return strings.HasPrefix(key.Value, "sk-mirra-") || (key.BaseURL != "" && key.BaseURL == g.cfg.publicBase)
}
