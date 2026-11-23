package main

import (
	"context"
	"database/sql"
	"errors"
	"net/http"
	"slices"
	"time"
)

func validateTierKeyAssignments(current, proposed *modelSettings, keys []accessKey) error {
	for _, key := range keys {
		id := current.keyTierID(key)
		if _, ok := proposed.tier(id); id == "" || !ok {
			return errors.New("该档位仍绑定分发密钥，请选择替代档位后删除")
		}
	}
	return nil
}

func tierMutationError(w http.ResponseWriter, err error) {
	status := http.StatusBadRequest
	switch {
	case errors.Is(err, errModelsChanged):
		status = http.StatusConflict
	case errors.Is(err, sql.ErrNoRows):
		status = http.StatusNotFound
		err = errors.New("档位不存在")
	case errors.Is(err, errModelsStorage):
		status = http.StatusServiceUnavailable
	}
	jsonError(w, status, err.Error())
}

func (g *gateway) deleteTier(ctx context.Context, id, replacement string, revision int64) (*modelSettings, error) {
	g.modelMu.Lock()
	saved, snapshots, err := g.deleteTierLocked(ctx, id, replacement, revision, time.Now())
	g.modelMu.Unlock()
	if err != nil {
		return nil, err
	}
	// Notify only committed changes. Callback state reads must not run while the
	// single SQLite connection is held by the migration transaction.
	for keyID, snapshot := range snapshots {
		g.notifyQuota(keyID, snapshot)
	}
	if g.onModelsChange != nil {
		g.onModelsChange(saved.Revision)
	}
	return saved, nil
}

// Caller holds modelMu. Key assignments and the model revision commit together;
// no key is removed and neither quota periods nor monetary limits are reset.
func (g *gateway) deleteTierLocked(ctx context.Context, id, replacement string, revision int64, now time.Time) (*modelSettings, map[string]object, error) {
	current := g.appModels()
	if revision != current.Revision {
		return nil, nil, errModelsChanged
	}
	if id == "" {
		return nil, nil, errors.New("请选择要删除的档位")
	}
	proposed := *current
	proposed.ensureTiers()
	index := slices.IndexFunc(proposed.Tiers, func(t featureTier) bool { return t.ID == id })
	if index < 0 {
		return nil, nil, sql.ErrNoRows
	}
	if replacement != "" {
		if _, exists := current.tier(replacement); !exists || replacement == id {
			return nil, nil, errors.New("替代档位须为另一个现存档位")
		}
	}
	proposed.Tiers = slices.Delete(slices.Clone(proposed.Tiers), index, index+1)
	prepared, err := prepareModels(current, proposed)
	if err != nil {
		return nil, nil, err
	}
	tx, err := g.store.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, nil, errModelsStorage
	}
	defer tx.Rollback()
	keys, err := listKeys(ctx, tx)
	if err != nil {
		return nil, nil, errModelsStorage
	}
	snapshots := map[string]object{}
	for i, key := range keys {
		if current.keyTierID(key) != id {
			continue
		}
		if replacement == "" {
			return nil, nil, errors.New("该档位仍绑定分发密钥，请选择替代档位后删除")
		}
		key.Policy.TierID = replacement
		if _, err := tx.ExecContext(ctx, "UPDATE access_keys SET policy=? WHERE id=?", key.Policy.encode(), key.ID); err != nil {
			return nil, nil, errModelsStorage
		}
		if err := quotaEvent(ctx, tx, key.ID, "tier_changed", "permissions", 0, 0, now); err != nil {
			return nil, nil, errModelsStorage
		}
		snapshot, err := g.store.usageTx(ctx, tx, key, now)
		if err != nil {
			return nil, nil, errModelsStorage
		}
		snapshots[key.ID] = snapshot
		keys[i] = key
	}
	if err := validateTierKeyAssignments(current, &prepared, keys); err != nil {
		return nil, nil, err
	}
	if err := writeModels(ctx, tx, &prepared); err != nil {
		return nil, nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, nil, errModelsStorage
	}
	g.modelProfile.Store(&prepared)
	return &prepared, snapshots, nil
}
