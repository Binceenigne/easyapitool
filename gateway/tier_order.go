package main

import (
	"errors"
	"net/http"
	"slices"
)

// An empty saved order uses permission sorting. Once reordered, edits preserve
// that sequence, deletion removes the missing ID, and new tiers append at end.
func (s *modelSettings) applyTierOrder() error {
	if len(s.TierOrder) == 0 {
		s.TierOrder = nil
		s.Tiers = sortedTiers(s.Tiers)
		return nil
	}
	if len(s.TierOrder) > 64 {
		return errors.New("档位排序最多支持 64 项")
	}
	byID := make(map[string]featureTier, len(s.Tiers))
	for _, tier := range s.Tiers {
		byID[tier.ID] = tier
	}
	ordered := make([]featureTier, 0, len(s.Tiers))
	seen := make(map[string]bool, len(s.TierOrder))
	for _, id := range s.TierOrder {
		if !modelPattern.MatchString(id) || seen[id] {
			return errors.New("档位顺序含有无效或重复的标识")
		}
		seen[id] = true
		if tier, ok := byID[id]; ok {
			ordered = append(ordered, tier)
		}
	}
	for _, tier := range s.Tiers {
		if !seen[tier.ID] {
			ordered = append(ordered, tier)
		}
	}
	s.Tiers = ordered
	s.TierOrder = make([]string, 0, len(ordered))
	for _, tier := range ordered {
		s.TierOrder = append(s.TierOrder, tier.ID)
	}
	return nil
}

func (a *admin) orderTiers(w http.ResponseWriter, r *http.Request) {
	var input struct {
		IDs       []string `json:"ids"`
		Automatic bool     `json:"automatic"`
		Revision  int64    `json:"revision"`
	}
	if !decodeRequest(w, r, 16<<10, &input) {
		return
	}
	profile := *a.gateway.appModels()
	if profile.Revision != input.Revision {
		jsonError(w, http.StatusConflict, errModelsChanged.Error())
		return
	}
	if input.Automatic {
		if len(input.IDs) > 0 {
			jsonError(w, http.StatusBadRequest, "自动排序不能同时指定手动顺序")
			return
		}
		profile.TierOrder = []string{}
	} else {
		if len(input.IDs) != len(profile.Tiers) {
			jsonError(w, http.StatusBadRequest, "请提供全部档位的新顺序")
			return
		}
		seen := make(map[string]bool, len(input.IDs))
		for _, id := range input.IDs {
			if _, ok := profile.tier(id); !ok || id == "" || seen[id] {
				jsonError(w, http.StatusBadRequest, "档位顺序无效、重复或已过期，请刷新后重试")
				return
			}
			seen[id] = true
		}
		profile.TierOrder = slices.Clone(input.IDs)
	}
	saved, err := a.gateway.saveModels(r.Context(), profile)
	if err != nil {
		tierMutationError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, saved)
}
