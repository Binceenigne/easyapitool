package main

import (
	"net/http"
	"slices"
	"sort"
)

// These are derived choices for an editor, not another stored policy. The model
// lists stay global so deselecting a model never removes the way to select it.
// Native allowlists are unions of the selected models' documented abilities;
// dependent controls also follow the selected formats and reasoning efforts.
type tierCapabilityOptions struct {
	ImageModels      []string `json:"imageModels"`
	ReasoningModels  []string `json:"reasoningModels"`
	ReasoningModes   []string `json:"reasoningModes"`
	ReasoningEfforts []string `json:"reasoningEfforts"`
	Qualities        []string `json:"qualities"`
	Complexities     []string `json:"complexities"`
	Formats          []string `json:"formats"`
	Advanced         bool     `json:"advanced"`
	Transparency     bool     `json:"transparency"`
	WebSearch        bool     `json:"webSearch"`
	PromptPolish     bool     `json:"promptPolish"`
	ImageEditing     bool     `json:"imageEditing"`
	VisualSkills     bool     `json:"visualSkills"`
	MaxResolution    int      `json:"maxResolution"`
	MaxImages        int      `json:"maxImages"`
	MaxReferences    int      `json:"maxReferences"`
	MaxGIFFrames     int      `json:"maxGifFrames"`
}

func configuredReasoningModels(settings *modelSettings) []string {
	result := []string{}
	add := func(model string) {
		if model != "" && !slices.Contains(result, model) {
			result = append(result, model)
		}
	}
	for _, slot := range []string{"luna", "terra", "sol"} {
		add(settings.Reasoning[slot].Model)
	}
	for _, mode := range []string{"instant", "flash", "medium", "high", "extra", "max"} {
		add(settings.ReasoningModes[mode].Model)
	}
	// Retain explicitly configured capability entries for model families that
	// are exposed outside the three slots, without depending on map iteration.
	extra := make([]string, 0, len(settings.ReasoningCapabilities))
	for model := range settings.ReasoningCapabilities {
		if model != settings.SVG.Model {
			extra = append(extra, model)
		}
	}
	sort.Strings(extra)
	for _, model := range extra {
		add(model)
	}
	return result
}

func tierOptions(settings *modelSettings, draft *featureTier) (tierCapabilityOptions, featureTier) {
	options := tierCapabilityOptions{
		ImageModels: append([]string{}, settings.ImageModels...), ReasoningModels: configuredReasoningModels(settings),
		ReasoningModes: []string{}, ReasoningEfforts: []string{}, Qualities: []string{}, Complexities: []string{}, Formats: []string{},
	}
	effective := *draft
	effective.ImageModels = allowedSubset(options.ImageModels, draft.ImageModels)
	options.VisualSkills = len(effective.ImageModels) > 0
	visualSkills := draft.visualSkillsEnabled() && options.VisualSkills
	effective.VisualSkills = &visualSkills
	effective.ReasoningModels = allowedSubset(options.ReasoningModels, draft.ReasoningModels)
	qualities, efforts, formats := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, model := range effective.ImageModels {
		features, known := documentedImageFeatures(model)
		if !known {
			continue // Unknown models do not acquire guessed native abilities.
		}
		for _, value := range features.Quality {
			qualities[value] = true
		}
		for _, format := range []string{"png", "jpeg", "webp"} {
			formats[format] = true
		}
		if features.SVG && settings.SVG.Enabled {
			formats["svg"] = true
		}
		if features.Animation {
			formats["gif"] = true
			options.MaxGIFFrames = 16
		}
		if features.Complexity {
			options.Complexities = []string{"low", "medium", "high"}
		}
		resolution := 1536
		if features.SizeMode == "flexible" {
			resolution = 3840
		}
		options.MaxResolution = max(options.MaxResolution, resolution)
		// These limits are the gateway's supported product controls, rather
		// than a claim about an upstream provider's larger request limits.
		options.MaxImages, options.MaxReferences = 9, 16
		options.Transparency, options.ImageEditing = true, true
	}
	for _, quality := range []string{"low", "medium", "high", "xhigh", "max"} {
		if qualities[quality] {
			options.Qualities = append(options.Qualities, quality)
		}
	}
	for _, format := range []string{"png", "jpeg", "webp", "svg", "gif"} {
		if formats[format] {
			options.Formats = append(options.Formats, format)
		}
	}
	effective.Formats = allowedSubset(options.Formats, draft.Formats)
	options.Transparency = options.Transparency && (slices.Contains(effective.Formats, "png") || slices.Contains(effective.Formats, "webp"))
	options.ImageEditing = options.ImageEditing && (slices.Contains(effective.Formats, "png") || slices.Contains(effective.Formats, "jpeg") || slices.Contains(effective.Formats, "webp") || slices.Contains(effective.Formats, "gif"))
	if !options.ImageEditing {
		options.MaxReferences = 0
	}
	if !slices.Contains(effective.Formats, "gif") {
		options.MaxGIFFrames = 0
	}
	for _, model := range effective.ReasoningModels {
		if capabilities, known := documentedReasoning(model); known {
			for _, effort := range capabilities.Efforts {
				efforts[effort] = true
			}
		}
	}
	for _, effort := range []string{"none", "minimal", "low", "medium", "high", "xhigh", "max"} {
		if efforts[effort] {
			options.ReasoningEfforts = append(options.ReasoningEfforts, effort)
		}
	}
	effective.ReasoningEfforts = allowedSubset(options.ReasoningEfforts, draft.ReasoningEfforts)
	options.Advanced = len(effective.ReasoningEfforts) > 0
	options.WebSearch, options.PromptPolish = options.Advanced, options.Advanced
	for _, mode := range []string{"instant", "flash", "medium", "high", "extra", "max"} {
		// The clients always retain direct generation when the configured
		// Instant language preset is unavailable to this key.
		if mode == "instant" {
			if len(effective.ImageModels) > 0 {
				options.ReasoningModes = append(options.ReasoningModes, mode)
			}
			continue
		}
		preset, configured := settings.ReasoningModes[mode]
		if !configured {
			continue
		}
		capabilities, known := documentedReasoning(preset.Model)
		effort := effectiveReasoningEffort(preset.Model, preset.Effort)
		if slices.Contains(effective.ReasoningModels, preset.Model) && known && slices.Contains(capabilities.Efforts, effort) && slices.Contains(effective.ReasoningEfforts, effort) {
			options.ReasoningModes = append(options.ReasoningModes, mode)
		}
	}
	effective.ReasoningModes = allowedSubset(options.ReasoningModes, draft.ReasoningModes)
	// Direct generation is an existing non-disableable baseline, not an
	// exception that enables any forbidden language model or preset.
	if len(effective.ImageModels) > 0 && !slices.Contains(effective.ReasoningModes, "instant") {
		effective.ReasoningModes = append([]string{"instant"}, effective.ReasoningModes...)
	}
	effective.Qualities = allowedSubset(options.Qualities, draft.Qualities)
	effective.Complexities = allowedSubset(options.Complexities, draft.Complexities)
	effective.Advanced = draft.Advanced && options.Advanced && len(effective.ReasoningEfforts) > 0
	effective.WebSearch = draft.WebSearch && options.WebSearch && len(effective.ReasoningEfforts) > 0
	effective.PromptPolish = draft.PromptPolish && options.PromptPolish && len(effective.ReasoningEfforts) > 0
	effective.Transparency = draft.Transparency && options.Transparency
	effective.ImageEditing = draft.ImageEditing && options.ImageEditing
	// Keep the published featureTier schema valid even when no model is selected.
	// Empty model/format allowlists, rather than these harmless minima, deny use.
	effective.MaxResolution = 1536
	for _, resolution := range []int{2560, 3840} {
		if resolution <= min(draft.MaxResolution, options.MaxResolution) {
			effective.MaxResolution = resolution
		}
	}
	effective.MaxImages = max(1, min(draft.MaxImages, options.MaxImages))
	effective.MaxReferences = max(0, min(draft.MaxReferences, options.MaxReferences))
	if !effective.ImageEditing {
		effective.MaxReferences = 0
	}
	effective.MaxGIFFrames = 4
	for _, frames := range []int{9, 12, 16} {
		if frames <= min(draft.MaxGIFFrames, options.MaxGIFFrames) {
			effective.MaxGIFFrames = frames
		}
	}
	return options, effective
}

func effectiveTiers(settings *modelSettings) []featureTier {
	result := make([]featureTier, 0, len(settings.Tiers))
	for _, tier := range settings.Tiers {
		_, effective := tierOptions(settings, &tier)
		result = append(result, effective)
	}
	return result
}

// Preserve inheritance when saving. Only explicitly selected impossible values
// are removed; deriving an effective view never rewrites the stored policy.
func normalizeTierChoices(settings *modelSettings, tier featureTier) featureTier {
	_, effective := tierOptions(settings, &tier)
	inherit := func(raw, resolved []string) []string {
		if slices.Contains(raw, "*") {
			return raw
		}
		return resolved
	}
	effective.ImageModels = inherit(tier.ImageModels, effective.ImageModels)
	effective.ReasoningModels = inherit(tier.ReasoningModels, effective.ReasoningModels)
	effective.ReasoningModes = inherit(tier.ReasoningModes, effective.ReasoningModes)
	effective.ReasoningEfforts = inherit(tier.ReasoningEfforts, effective.ReasoningEfforts)
	effective.Qualities = inherit(tier.Qualities, effective.Qualities)
	effective.Complexities = inherit(tier.Complexities, effective.Complexities)
	effective.Formats = inherit(tier.Formats, effective.Formats)
	return effective
}

func (a *admin) tierOptions(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Tier     *featureTier `json:"tier"`
		Revision *int64       `json:"revision"`
	}
	if !decodeRequest(w, r, 64<<10, &input) {
		return
	}
	if input.Tier == nil || input.Revision == nil || *input.Revision < 0 {
		jsonError(w, http.StatusBadRequest, "请提供档位草稿和配置版本")
		return
	}
	settings := a.gateway.appModels()
	if *input.Revision != settings.Revision {
		jsonError(w, http.StatusConflict, errModelsChanged.Error())
		return
	}
	options, effective := tierOptions(settings, input.Tier)
	writeJSON(w, http.StatusOK, object{"revision": settings.Revision, "options": options, "tier": effective})
}
