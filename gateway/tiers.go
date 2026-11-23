package main

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"net/http"
	"slices"
	"sort"
	"strings"
)

type featureTier struct {
	ID               string           `json:"id"`
	Name             string           `json:"name"`
	Description      string           `json:"description"`
	ImageModels      []string         `json:"imageModels"`
	ReasoningModels  []string         `json:"reasoningModels"`
	ReasoningModes   []string         `json:"reasoningModes"`
	ReasoningEfforts []string         `json:"reasoningEfforts"`
	Qualities        []string         `json:"qualities"`
	Complexities     []string         `json:"complexities"`
	Formats          []string         `json:"formats"`
	Advanced         bool             `json:"advanced"`
	Transparency     bool             `json:"transparency"`
	WebSearch        bool             `json:"webSearch"`
	PromptPolish     bool             `json:"promptPolish"`
	ImageEditing     bool             `json:"imageEditing"`
	MaxResolution    int              `json:"maxResolution"`
	MaxImages        int              `json:"maxImages"`
	MaxReferences    int              `json:"maxReferences"`
	MaxGIFFrames     int              `json:"maxGifFrames"`
	DefaultQuota     tierDefaultQuota `json:"defaultQuota"`
	RPMLimit         perMinuteLimit   `json:"rpmLimit"`
}

func fullTier() featureTier {
	return featureTier{ID: "default", Name: "完整权限", Description: "所有已开放模型与功能",
		ImageModels: []string{"*"}, ReasoningModels: []string{"*"}, ReasoningModes: []string{"*"}, ReasoningEfforts: []string{"*"},
		Qualities: []string{"*"}, Complexities: []string{"*"}, Formats: []string{"*"}, Advanced: true, Transparency: true,
		WebSearch: true, PromptPolish: true, ImageEditing: true, MaxResolution: 3840, MaxImages: 9, MaxReferences: 16, MaxGIFFrames: 16}
}

func (s *modelSettings) keyTierID(k accessKey) string {
	if k.Policy.TierID != "" {
		return k.Policy.TierID
	}
	return s.defaultTierID()
}

func (s *modelSettings) defaultTierID() string {
	if s.Tiers == nil {
		return "default"
	}
	for _, tier := range s.Tiers {
		if tier.ID == "default" {
			return tier.ID
		}
	}
	if len(s.Tiers) == 0 {
		return ""
	}
	first := sortedTiers(s.Tiers)[0]
	// Stable display sorting alone cannot distinguish equally ranked, equally
	// named tiers. Retain the saved winner of that tie so dragging columns never
	// changes an implicit key's permissions or RPM limit.
	for _, tier := range s.Tiers {
		if tier.ID == s.DefaultTierID && tier.Name == first.Name && tierScore(tier) == tierScore(first) {
			return tier.ID
		}
	}
	return first.ID
}
func permits(values []string, value string) bool {
	return slices.Contains(values, "*") || slices.Contains(values, value)
}
func allowedSubset(values, allowed []string) []string {
	result := []string{}
	for _, value := range values {
		if permits(allowed, value) {
			result = append(result, value)
		}
	}
	return result
}

func (s *modelSettings) ensureTiers() {
	if s.Tiers == nil {
		s.Tiers = []featureTier{fullTier()}
	}
	s.DefaultTierID = s.defaultTierID()
}
func (s *modelSettings) tier(id string) (featureTier, bool) {
	if id == "" {
		id = s.defaultTierID()
	}
	if s.Tiers == nil && id == "default" {
		return fullTier(), true
	}
	for _, t := range s.Tiers {
		if t.ID == id {
			return t, true
		}
	}
	return featureTier{}, false
}

func validateTier(t featureTier) error {
	if !modelPattern.MatchString(t.ID) || len(strings.TrimSpace(t.Name)) == 0 || len(t.Name) > 120 || len(t.Description) > 500 {
		return errors.New("请填写档位名称和有效标识")
	}
	if err := t.DefaultQuota.validate(); err != nil {
		return err
	}
	if t.RPMLimit < 0 || t.RPMLimit > 1_000_000 {
		return errors.New("RPM 须为 0 至 1000000 的整数，0 表示不限")
	}
	for _, list := range [][]string{t.ImageModels, t.ReasoningModels, t.ReasoningModes, t.ReasoningEfforts, t.Qualities, t.Complexities, t.Formats} {
		if len(list) > 128 {
			return errors.New("每组权限最多支持 128 项")
		}
		seen := map[string]bool{}
		for _, value := range list {
			if (value != "*" && !modelPattern.MatchString(value)) || seen[value] {
				return errors.New("权限选项无效或重复")
			}
			seen[value] = true
		}
		if slices.Contains(list, "*") && len(list) != 1 {
			return errors.New("全部权限不能与指定选项同时保存")
		}
	}
	for _, item := range []struct{ values, allowed []string }{
		{t.ReasoningModes, []string{"instant", "flash", "medium", "high", "extra", "max"}},
		{t.ReasoningEfforts, []string{"none", "minimal", "low", "medium", "high", "xhigh", "max"}},
		{t.Qualities, []string{"low", "medium", "high", "xhigh", "max"}},
		{t.Complexities, []string{"low", "medium", "high"}},
		{t.Formats, []string{"png", "jpeg", "webp", "svg", "gif"}},
	} {
		for _, v := range item.values {
			if v != "*" && !slices.Contains(item.allowed, v) {
				return errors.New("档位含有不支持的功能选项")
			}
		}
	}
	if !slices.Contains([]int{1536, 2560, 3840}, t.MaxResolution) || t.MaxImages < 1 || t.MaxImages > 9 || t.MaxReferences < 0 || t.MaxReferences > 16 || !slices.Contains([]int{4, 9, 12, 16}, t.MaxGIFFrames) {
		return errors.New("请检查分辨率、张数、参考图和 GIF 帧数上限")
	}
	return nil
}

func validateTiers(tiers []featureTier) error {
	if len(tiers) > 64 {
		return errors.New("最多支持 64 个功能档位")
	}
	seen := map[string]bool{}
	for _, t := range tiers {
		if err := validateTier(t); err != nil {
			return err
		}
		if seen[t.ID] {
			return errors.New("档位标识重复")
		}
		seen[t.ID] = true
	}
	return nil
}

func tierScore(t featureTier) int {
	count := func(v []string) int {
		if slices.Contains(v, "*") {
			return 256
		}
		return len(v)
	}
	score := t.MaxResolution/16 + t.MaxImages*4
	if t.ImageEditing {
		score += t.MaxReferences * 2
	}
	if permits(t.Formats, "gif") {
		score += t.MaxGIFFrames
	}
	for _, v := range [][]string{t.ImageModels, t.ReasoningModels, t.ReasoningModes, t.ReasoningEfforts, t.Qualities, t.Complexities, t.Formats} {
		score += count(v) * 8
	}
	for _, enabled := range []bool{t.Advanced, t.Transparency, t.WebSearch, t.PromptPolish, t.ImageEditing} {
		if enabled {
			score += 64
		}
	}
	return score
}

func sortedTiers(tiers []featureTier) []featureTier {
	if len(tiers) == 0 {
		return []featureTier{}
	}
	result := slices.Clone(tiers)
	sort.SliceStable(result, func(i, j int) bool {
		a, b := tierScore(result[i]), tierScore(result[j])
		if a != b {
			return a < b
		}
		return result[i].Name < result[j].Name
	})
	return result
}

type tierError struct{ feature string }

func (e *tierError) Error() string {
	return "当前密钥档位未开放" + e.feature + "，请调整选项或联系管理员"
}
func denied(feature string) error { return &tierError{feature} }

func presetPermitted(t featureTier, p modelPreset) bool {
	return p.Model == "" || (permits(t.ReasoningModels, p.Model) && permits(t.ReasoningEfforts, effectiveReasoningEffort(p.Model, p.Effort)))
}

// The same profile is used for authorization and both clients' periodic updates.
func (g *gateway) modelsForKey(k accessKey) (*modelSettings, error) {
	current := g.appModels()
	t, ok := current.tier(current.keyTierID(k))
	if !ok {
		return nil, denied("此档位")
	}
	profile := *current
	profile.Tiers = nil
	profile.TierOrder = nil
	profile.Catalog = catalogForModels(current)
	profile.Tier = &t
	profile.ImageModels = allowedSubset(current.ImageModels, t.ImageModels)
	profile.ImageFeatures = maps.Clone(current.ImageFeatures)
	if !slices.Contains(profile.ImageModels, profile.DefaultImage) {
		profile.DefaultImage = ""
		if len(profile.ImageModels) > 0 {
			profile.DefaultImage = profile.ImageModels[0]
		}
	}
	for model, f := range profile.ImageFeatures {
		if !slices.Contains(profile.ImageModels, model) {
			delete(profile.ImageFeatures, model)
			continue
		}
		f.Quality = allowedSubset(f.Quality, t.Qualities)
		f.Complexity = f.Complexity && len(allowedSubset([]string{"low", "medium", "high"}, t.Complexities)) > 0
		f.Animation = f.Animation && permits(t.Formats, "gif")
		f.SVG = f.SVG && permits(t.Formats, "svg")
		profile.ImageFeatures[model] = f
	}
	profile.SVG.Enabled = profile.SVG.Enabled && permits(t.Formats, "svg")
	profile.SVG.Effort = effectiveReasoningEffort(profile.SVG.Model, profile.SVG.Effort)
	profile.Reasoning = maps.Clone(current.Reasoning)
	for slot, p := range profile.Reasoning {
		if !permits(t.ReasoningModels, p.Model) {
			delete(profile.Reasoning, slot)
			continue
		}
		p.Effort = effectiveReasoningEffort(p.Model, p.Effort)
		profile.Reasoning[slot] = p
	}
	profile.ReasoningModes = maps.Clone(current.ReasoningModes)
	for mode, p := range profile.ReasoningModes {
		p.Effort = effectiveReasoningEffort(p.Model, p.Effort)
		if mode == "instant" {
			if p.Model == "" || !permits(t.ReasoningModes, mode) || !presetPermitted(t, p) {
				p = modelPreset{Effort: "none"}
			}
			profile.ReasoningModes[mode] = p
			continue
		}
		if !permits(t.ReasoningModes, mode) || !presetPermitted(t, p) {
			delete(profile.ReasoningModes, mode)
			continue
		}
		profile.ReasoningModes[mode] = p
	}
	profile.ReasoningCapabilities = maps.Clone(current.ReasoningCapabilities)
	for model, c := range profile.ReasoningCapabilities {
		c.Efforts = allowedSubset(c.Efforts, t.ReasoningEfforts)
		if !slices.Contains(c.Efforts, c.Default) {
			c.Default = ""
			if len(c.Efforts) > 0 {
				c.Default = c.Efforts[0]
			}
		}
		profile.ReasoningCapabilities[model] = c
	}
	if t.PromptPolish {
		profile.Polish = authorizedUtilityPreset(&profile, g.cfg.polishModel, "medium", []string{"terra", "luna", "sol"})
	}
	if t.ImageEditing {
		profile.Continuation = authorizedUtilityPreset(&profile, "", "low", []string{"luna", "terra", "sol"})
	}
	return &profile, nil
}

func authorizedUtilityPreset(profile *modelSettings, preferredModel, preferredEffort string, slots []string) modelPreset {
	models := []string{}
	if preferredModel != "" {
		models = append(models, preferredModel)
	}
	for _, slot := range slots {
		if p, ok := profile.Reasoning[slot]; ok {
			models = append(models, p.Model)
		}
	}
	for _, model := range models {
		if !permits(profile.Tier.ReasoningModels, model) {
			continue
		}
		efforts := []string{"none", "minimal", "low", "medium", "high", "xhigh", "max"}
		if cap, ok := profile.ReasoningCapabilities[model]; ok {
			efforts = cap.Efforts
		}
		efforts = allowedSubset(efforts, profile.Tier.ReasoningEfforts)
		for _, preferred := range []string{preferredEffort, "medium"} {
			if slices.Contains(efforts, preferred) {
				return modelPreset{Model: model, Effort: preferred}
			}
		}
		if len(efforts) > 0 {
			return modelPreset{Model: model, Effort: efforts[0]}
		}
	}
	return modelPreset{}
}

func (g *gateway) checkImageTier(k accessKey, o imageOptions, refs int) error {
	if k.ID == "" {
		return nil
	}
	p, err := g.modelsForKey(k)
	if err != nil {
		return err
	}
	t := *p.Tier
	if !slices.Contains(p.ImageModels, o.model) {
		return denied("此生图模型")
	}
	if !permits(t.Formats, o.format) {
		return denied("此输出格式")
	}
	if !permits(t.Qualities, o.quality) && o.quality != "auto" {
		return denied("此图片质量")
	}
	if o.quality == "auto" && !permits(t.Qualities, "*") {
		return denied("自动图片质量")
	}
	if o.format != "svg" {
		if o.complexity != "" && !permits(t.Complexities, o.complexity) {
			return denied("此复杂度")
		}
		if p.Catalog.ImageFeatures[o.model].Complexity && o.complexity == "" && !permits(t.Complexities, "*") {
			return denied("模型默认复杂度，请明确选择档位允许的复杂度")
		}
	}
	if (o.background == "transparent" || (o.background == "auto" && oneOf(o.format, "png", "webp"))) && !t.Transparency {
		return denied("透明背景")
	}
	if o.n > t.MaxImages {
		return denied(fmt.Sprintf("一次生成超过 %d 张", t.MaxImages))
	}
	if refs > 0 && (!t.ImageEditing || refs > t.MaxReferences) {
		return denied("此参考图数量或图片编辑")
	}
	if o.frameCount != nil && *o.frameCount > t.MaxGIFFrames {
		return denied("此 GIF 帧数")
	}
	if o.format == "gif" && o.frameCount == nil && t.MaxGIFFrames < 9 {
		return denied("默认 GIF 帧数")
	}
	if o.size == "auto" {
		if t.MaxResolution < 3840 {
			return denied("未指定尺寸的生成，请选择档位允许的分辨率")
		}
		return nil
	}
	var width, height int
	if _, err := fmt.Sscanf(o.size, "%dx%d", &width, &height); err == nil && max(width, height) > t.MaxResolution {
		return denied("此分辨率")
	}
	return nil
}

func (g *gateway) checkReasoningTier(k accessKey, model, effort, mode string, web bool) error {
	if k.ID == "" {
		return nil
	}
	p, err := g.modelsForKey(k)
	if err != nil {
		return err
	}
	t := *p.Tier
	if web && !t.WebSearch {
		return denied("联网搜索")
	}
	if mode == "polish" && !t.PromptPolish {
		return denied("提示词润色")
	}
	if mode == "continuation" && !t.ImageEditing {
		return denied("续作与图片编辑")
	}
	if mode == "advanced" && !t.Advanced {
		return denied("高级模式")
	}
	if model == "" {
		return denied("未指定思维模型的调用")
	}
	effort = effectiveReasoningEffort(model, effort)
	if mode == "svg" && p.SVG.Enabled && model == p.SVG.Model && effort == p.SVG.Effort {
		return nil
	}
	if !permits(t.ReasoningModels, model) || !permits(t.ReasoningEfforts, effort) {
		return denied("此思维模型或深度")
	}
	switch mode {
	case "polish":
		if model != p.Polish.Model || effort != p.Polish.Effort {
			return denied("此提示词润色模型组合")
		}
	case "continuation":
		if model != p.Continuation.Model || effort != p.Continuation.Effort {
			return denied("此续作规划模型组合")
		}
	case "advanced":
	case "":
		if !t.Advanced {
			allowed := false
			for _, preset := range p.ReasoningModes {
				allowed = allowed || (preset.Model == model && preset.Effort == effort)
			}
			if !allowed {
				return denied("高级思维组合")
			}
		}
	default:
		preset, ok := p.ReasoningModes[mode]
		if !ok || preset.Model != model || preset.Effort != effort {
			return denied("此思维深度档或模型组合")
		}
	}
	return nil
}

func (a *admin) saveTier(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Tier              featureTier `json:"tier"`
		Revision          int64       `json:"revision"`
		Delete            bool        `json:"delete"`
		ReplacementTierID string      `json:"replacementTierId"`
	}
	if !decodeRequest(w, r, 64<<10, &input) {
		return
	}
	if input.Delete {
		saved, err := a.gateway.deleteTier(r.Context(), input.Tier.ID, input.ReplacementTierID, input.Revision)
		if err != nil {
			tierMutationError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, saved)
		return
	}
	profile := *a.gateway.appModels()
	profile.ensureTiers()
	if profile.Revision != input.Revision {
		jsonError(w, 409, errModelsChanged.Error())
		return
	}
	if input.Tier.ID == "" {
		input.Tier.ID = "tier-" + randomID(12)
	}
	index := slices.IndexFunc(profile.Tiers, func(t featureTier) bool { return t.ID == input.Tier.ID })
	profile.Tiers = slices.Clone(profile.Tiers)
	if index < 0 {
		profile.Tiers = append(profile.Tiers, input.Tier)
	} else {
		profile.Tiers[index] = input.Tier
	}
	saved, err := a.gateway.saveModels(r.Context(), profile)
	if err != nil {
		tierMutationError(w, err)
		return
	}
	writeJSON(w, 200, saved)
}

func (g *gateway) validateTierAssignments(ctx context.Context, proposed *modelSettings) error {
	keys, err := g.store.listKeys(ctx)
	if err != nil {
		return err
	}
	return validateTierKeyAssignments(g.appModels(), proposed, keys)
}
