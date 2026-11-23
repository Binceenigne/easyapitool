package main

import "net/http"

// Use the same policy projection as a real distributed key, without issuing a
// credential, admitting a model request, or changing the tier configuration.
func (a *admin) previewTier(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		jsonError(w, http.StatusNotFound, "档位不存在，请刷新后重试")
		return
	}
	profile, err := a.gateway.modelsForKey(accessKey{Policy: keyPolicy{TierID: id}})
	if err != nil {
		jsonError(w, http.StatusNotFound, "档位不存在或已删除，请刷新后重试")
		return
	}
	writeJSON(w, http.StatusOK, profile)
}
