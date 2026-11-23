package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"time"
)

// This record is independent of the editable model configuration. Neither model
// edits nor deleting an unpublished source tier can alter an existing snapshot.
type landingTierSnapshot struct {
	Enabled        bool          `json:"enabled"`
	TierIDs        []string      `json:"tierIds"`
	Revision       int64         `json:"revision"`
	PublishedAt    *time.Time    `json:"publishedAt"`
	SourceRevision int64         `json:"sourceRevision"`
	Tiers          []featureTier `json:"tiers"`
}

// Explicitly enumerate the public fields. Administration-only metadata added to
// featureTier in the future must not be published by embedding that type here.
type publicFeatureTier struct {
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

func publicTiers(tiers []featureTier) []publicFeatureTier {
	result := make([]publicFeatureTier, 0, len(tiers))
	list := func(values []string) []string { return append([]string{}, values...) }
	for _, t := range tiers {
		result = append(result, publicFeatureTier{
			ID: t.ID, Name: t.Name, Description: t.Description,
			ImageModels: list(t.ImageModels), ReasoningModels: list(t.ReasoningModels),
			ReasoningModes: list(t.ReasoningModes), ReasoningEfforts: list(t.ReasoningEfforts),
			Qualities: list(t.Qualities), Complexities: list(t.Complexities), Formats: list(t.Formats),
			Advanced: t.Advanced, Transparency: t.Transparency, WebSearch: t.WebSearch,
			PromptPolish: t.PromptPolish, ImageEditing: t.ImageEditing,
			MaxResolution: t.MaxResolution, MaxImages: t.MaxImages, MaxReferences: t.MaxReferences,
			MaxGIFFrames: t.MaxGIFFrames, DefaultQuota: t.DefaultQuota, RPMLimit: t.RPMLimit,
		})
	}
	return result
}

func emptyLandingTiers() landingTierSnapshot {
	return landingTierSnapshot{TierIDs: []string{}, Tiers: []featureTier{}}
}

func (s landingTierSnapshot) adminView() object {
	return object{"enabled": s.Enabled, "tierIds": s.TierIDs, "revision": s.Revision,
		"publishedAt": s.PublishedAt, "sourceRevision": s.SourceRevision, "tiers": publicTiers(s.Tiers)}
}

func (s landingTierSnapshot) publicView() object {
	tiers := s.Tiers
	if !s.Enabled {
		tiers = nil
	}
	return object{"enabled": s.Enabled, "revision": s.Revision,
		"publishedAt": s.PublishedAt, "tiers": publicTiers(tiers)}
}

func (g *gateway) landingTiers(ctx context.Context) (landingTierSnapshot, error) {
	var data []byte
	err := g.store.db.QueryRowContext(ctx, "SELECT value FROM gateway_settings WHERE name='landing_tiers'").Scan(&data)
	if errors.Is(err, sql.ErrNoRows) {
		return emptyLandingTiers(), nil
	}
	if err != nil {
		return landingTierSnapshot{}, err
	}
	var snapshot landingTierSnapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return landingTierSnapshot{}, err
	}
	if snapshot.Revision < 1 || snapshot.SourceRevision < 0 || len(snapshot.Tiers) > 64 || len(snapshot.TierIDs) != len(snapshot.Tiers) ||
		(len(snapshot.Tiers) > 0 && snapshot.PublishedAt == nil) || (snapshot.Enabled && len(snapshot.Tiers) == 0) {
		return landingTierSnapshot{}, errLandingStorage
	}
	seen := map[string]bool{}
	for i, tier := range snapshot.Tiers {
		if err := validateTier(tier); err != nil || snapshot.TierIDs[i] != tier.ID || seen[tier.ID] {
			return landingTierSnapshot{}, errLandingStorage
		}
		seen[tier.ID] = true
	}
	if snapshot.TierIDs == nil {
		snapshot.TierIDs = []string{}
	}
	return snapshot, nil
}

var errLandingChanged = errors.New("落地页档位设置已更新，请刷新页面后重试")
var errLandingStorage = errors.New("落地页档位设置暂时无法读取或保存")

type landingTierUpdate struct {
	Action   string    `json:"action"`
	Enabled  *bool     `json:"enabled"`
	TierIDs  *[]string `json:"tierIds"`
	Revision *int64    `json:"revision"`
}

func (g *gateway) updateLandingTiers(ctx context.Context, input landingTierUpdate, now time.Time) (landingTierSnapshot, error) {
	if !oneOf(input.Action, "sync", "visibility") || input.Enabled == nil || input.Revision == nil || *input.Revision < 0 {
		return landingTierSnapshot{}, errors.New("请提供有效的操作、显示开关和配置版本")
	}
	// Model saves and tier deletion use this same lock. Capture one complete model
	// revision while serializing independent publication revisions.
	g.modelMu.Lock()
	defer g.modelMu.Unlock()
	current, err := g.landingTiers(ctx)
	if err != nil {
		return landingTierSnapshot{}, errLandingStorage
	}
	if *input.Revision != current.Revision {
		return landingTierSnapshot{}, errLandingChanged
	}
	if current.Revision == math.MaxInt64 {
		return landingTierSnapshot{}, errLandingStorage
	}
	next := current
	if input.Action == "sync" {
		if input.TierIDs == nil || len(*input.TierIDs) > 64 {
			return landingTierSnapshot{}, errors.New("请选择要同步的档位，最多支持 64 个")
		}
		models := g.appModels()
		selected := make([]featureTier, 0, len(*input.TierIDs))
		seen := map[string]bool{}
		for _, id := range *input.TierIDs {
			tier, ok := models.tier(id)
			if !ok || id == "" || seen[id] {
				return landingTierSnapshot{}, errors.New("所选档位无效、已删除或重复，请刷新后选择")
			}
			seen[id] = true
			selected = append(selected, tier)
		}
		// Publication follows the saved workspace order, independently of the
		// order in which the selection checkboxes were clicked.
		next.Tiers = make([]featureTier, 0, len(selected))
		for _, tier := range models.Tiers {
			if seen[tier.ID] {
				_, effective := tierOptions(models, &tier)
				next.Tiers = append(next.Tiers, effective)
			}
		}
		next.TierIDs = make([]string, 0, len(selected))
		for _, tier := range next.Tiers {
			next.TierIDs = append(next.TierIDs, tier.ID)
		}
		published := now.UTC()
		next.PublishedAt, next.SourceRevision = &published, models.Revision
	}
	next.Enabled = *input.Enabled
	if next.Enabled && len(next.Tiers) == 0 {
		return landingTierSnapshot{}, errors.New("请先同步至少一个档位，再开启落地页显示")
	}
	next.Revision++
	data, err := json.Marshal(next)
	if err != nil {
		return landingTierSnapshot{}, errLandingStorage
	}
	if _, err := g.store.db.ExecContext(ctx, `INSERT INTO gateway_settings(name,value) VALUES ('landing_tiers',?)
		ON CONFLICT(name) DO UPDATE SET value=excluded.value`, data); err != nil {
		return landingTierSnapshot{}, errLandingStorage
	}
	// Detached values keep callers from mutating the saved snapshot through a
	// shared model slice after publication.
	var detached landingTierSnapshot
	if err := json.Unmarshal(data, &detached); err != nil {
		return landingTierSnapshot{}, errLandingStorage
	}
	return detached, nil
}

func (a *admin) saveLandingTiers(w http.ResponseWriter, r *http.Request) {
	var input landingTierUpdate
	if !decodeRequest(w, r, 16<<10, &input) {
		return
	}
	snapshot, err := a.gateway.updateLandingTiers(r.Context(), input, time.Now())
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, errLandingChanged) {
			status = http.StatusConflict
		} else if errors.Is(err, errLandingStorage) {
			status = http.StatusServiceUnavailable
		}
		jsonError(w, status, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot.adminView())
}

func (g *gateway) publicLandingTiers(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	snapshot, err := g.landingTiers(r.Context())
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "暂时无法读取档位信息")
		return
	}
	writeJSON(w, http.StatusOK, snapshot.publicView())
}
