package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"time"
)

func (s *store) saveTerminalEvent(ctx context.Context, id credentialID, event object) error {
	data, err := json.Marshal(event)
	if err != nil {
		return err
	}
	if len(data) > 1<<20 {
		return errors.New("task terminal event exceeds limit")
	}
	sealed := s.seal(data, "task-terminal:"+id.owner+":"+id.id)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `INSERT INTO task_terminal_events(owner,task_id,created,value) VALUES(?,?,?,?)
		ON CONFLICT(owner,task_id) DO UPDATE SET created=excluded.created,value=excluded.value`, id.owner, id.id, time.Now().UnixMilli(), sealed); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM task_terminal_events WHERE created<? OR rowid IN
		(SELECT rowid FROM task_terminal_events ORDER BY created DESC LIMIT -1 OFFSET 10000)`, time.Now().Add(-7*24*time.Hour).UnixMilli()); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *store) terminalEvent(ctx context.Context, id credentialID) (object, error) {
	var sealed []byte
	if err := s.db.QueryRowContext(ctx, `SELECT value FROM task_terminal_events WHERE owner=? AND task_id=? AND created>?`, id.owner, id.id, time.Now().Add(-7*24*time.Hour).UnixMilli()).Scan(&sealed); err != nil {
		return nil, err
	}
	data, err := s.unseal(sealed, "task-terminal:"+id.owner+":"+id.id)
	if err != nil {
		return nil, err
	}
	var event object
	if err = json.Unmarshal(data, &event); err != nil {
		return nil, err
	}
	return event, nil
}

func (m *mobile) persistTerminal(id credentialID, event object) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := m.g.store.saveTerminalEvent(ctx, id, event); err != nil {
		log.Printf("cannot persist task terminal event: %T", err)
	}
}
