package main

import (
	"context"
	"database/sql"
	"time"
)

func usageRates(ctx context.Context, tx *sql.Tx, id string, now time.Time) (object, error) {
	day := midnight(now)
	week := day.AddDate(0, 0, -(int(day.Weekday())+6)%7)
	hour := now.Truncate(time.Hour)
	ten := now.Truncate(10 * time.Minute)
	start := week
	if earlier := hour.Add(-12 * time.Hour); earlier.Before(start) {
		start = earlier
	}
	type totals struct{ cost, calls, priced int64 }
	byTen := map[int64]totals{}
	rows, err := tx.QueryContext(ctx, `SELECT (created/600000)*600000,COALESCE(SUM(cost_micro),0),COUNT(*),COUNT(cost_micro)
		FROM requests WHERE key_id=? AND created>=? AND created<=? GROUP BY 1 ORDER BY 1`, id, start.UnixMilli(), now.UnixMilli())
	if err != nil {
		return nil, err
	}
	var daily, weekly int64
	for rows.Next() {
		var ts int64
		var t totals
		if err := rows.Scan(&ts, &t.cost, &t.calls, &t.priced); err != nil {
			rows.Close()
			return nil, err
		}
		byTen[ts] = t
		if ts >= day.UnixMilli() {
			daily += t.cost
		}
		if ts >= week.UnixMilli() {
			weekly += t.cost
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()
	buckets := func(end time.Time, span time.Duration) []object {
		result := make([]object, 0, 12)
		for i := 12; i > 0; i-- {
			from := end.Add(-time.Duration(i) * span)
			until := from.Add(span)
			var sum totals
			for at := from; at.Before(until); at = at.Add(10 * time.Minute) {
				v := byTen[at.UnixMilli()]
				sum.cost += v.cost
				sum.calls += v.calls
				sum.priced += v.priced
			}
			item := object{"startTimestamp": from.UnixMilli(), "endTimestamp": until.UnixMilli(), "status": "recorded", "cost": float64(sum.cost) / 1e6, "observedSeconds": int(span.Seconds())}
			if sum.calls > sum.priced {
				item["status"] = "estimated"
				if sum.priced == 0 {
					item["status"], item["cost"] = "unrecorded", nil
				}
			}
			result = append(result, item)
		}
		return result
	}
	intervals := object{}
	for name, span := range map[string]time.Duration{"10m": 10 * time.Minute, "1h": time.Hour} {
		var amount, calls, priced int64
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(SUM(cost_micro),0),COUNT(*),COUNT(cost_micro) FROM requests WHERE key_id=? AND created>? AND created<=?`, id, now.Add(-span).UnixMilli(), now.UnixMilli()).Scan(&amount, &calls, &priced); err != nil {
			return nil, err
		}
		item := object{"value": float64(amount) / 1e6, "status": "recorded", "observedSeconds": int(span.Seconds())}
		if priced < calls {
			item["status"] = "estimated"
			if priced == 0 {
				item["status"], item["value"] = "unrecorded", nil
			}
		}
		intervals[name] = item
	}
	avg := func(micro int64, start time.Time, label string) object {
		cost := float64(micro) / 1e6
		mins := max(1.0, now.Sub(start).Minutes())
		return object{"cost": cost, "avgMin": cost / mins, "avgHour": cost * 60 / mins, "label": label}
	}
	return object{"timezone": billingZone.String(), "source": "gateway", "intervals": intervals,
		"speed10m": asObject(intervals["10m"])["value"], "speed1h": asObject(intervals["1h"])["value"],
		"averages":  object{"today": avg(daily, day, "今日"), "week": avg(weekly, week, "本周")},
		"hourly12h": buckets(hour, time.Hour), "tenMinute2h": buckets(ten, 10*time.Minute)}, nil
}
