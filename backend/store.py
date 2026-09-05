from __future__ import annotations

from .common import *
from .platform import normalize_background_ui_mode, normalize_title_bar_mode, normalize_window_size
from .security import SecretProtector, default_secret_protector
from .usage import safe_float

class Store:
    def __init__(
        self,
        path: Path,
        secret_protector: SecretProtector | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secret_protector = secret_protector or default_secret_protector()
        self.lock = threading.RLock()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    secret_dpapi TEXT NOT NULL DEFAULT '',
                    secret_encrypted TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    sampled_at REAL NOT NULL,
                    total_cost REAL NOT NULL DEFAULT 0,
                    today_cost REAL NOT NULL DEFAULT 0,
                    quota_limit REAL NOT NULL DEFAULT 0,
                    quota_used REAL NOT NULL DEFAULT 0,
                    remaining REAL NOT NULL DEFAULT 0,
                    used_5h REAL NOT NULL DEFAULT 0,
                    used_1d REAL NOT NULL DEFAULT 0,
                    used_7d REAL NOT NULL DEFAULT 0,
                    today_requests INTEGER NOT NULL DEFAULT 0,
                    total_requests INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_key_time
                    ON usage_snapshots(key_id, sampled_at);
                CREATE TABLE IF NOT EXISTS daily_usage (
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    usage_date TEXT NOT NULL,
                    cost REAL NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key_id, usage_date)
                );
                CREATE TABLE IF NOT EXISTS alert_state (
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (key_id, metric)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(api_keys)").fetchall()
            }
            if "secret_encrypted" not in columns:
                db.execute(
                    "ALTER TABLE api_keys ADD COLUMN secret_encrypted TEXT NOT NULL DEFAULT ''"
                )
            if "secret_dpapi" not in columns:
                db.execute(
                    "ALTER TABLE api_keys ADD COLUMN secret_dpapi TEXT NOT NULL DEFAULT ''"
                )
            invalid_snapshot_ids: list[int] = []
            for row in db.execute("SELECT id,payload_json FROM usage_snapshots").fetchall():
                try:
                    payload = json.loads(row["payload_json"])
                    value = ((payload.get("usage") or {}).get("total") or {}).get("cost")
                    total_cost = float(value)
                    valid = math.isfinite(total_cost) and total_cost >= 0
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    valid = False
                if not valid:
                    invalid_snapshot_ids.append(int(row["id"]))
            if invalid_snapshot_ids:
                db.executemany(
                    "DELETE FROM usage_snapshots WHERE id=?",
                    ((snapshot_id,) for snapshot_id in invalid_snapshot_ids),
                )

    def add_key(self, name: str, secret: str, base_url: str) -> str:
        key_id = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO api_keys(id,name,secret_dpapi,secret_encrypted,base_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    key_id,
                    name,
                    "",
                    self.secret_protector.protect(secret),
                    base_url.rstrip("/"),
                    now,
                    now,
                ),
            )
        return key_id

    def import_environment_key(self) -> None:
        secret = os.environ.get("OPENAI_API_KEY", "").strip()
        if not secret:
            return
        with self.lock, self.connect() as db:
            existing = db.execute(
                "SELECT secret_dpapi,secret_encrypted FROM api_keys"
            ).fetchall()
            for row in existing:
                try:
                    encrypted = row["secret_encrypted"] or row["secret_dpapi"]
                    if encrypted and self.secret_protector.unprotect(encrypted) == secret:
                        return
                except (OSError, RuntimeError, ValueError):
                    continue
        self.add_key("环境变量 Key", secret, os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL))

    def list_key_records(self) -> list[sqlite3.Row]:
        with self.lock, self.connect() as db:
            return db.execute("SELECT * FROM api_keys ORDER BY created_at").fetchall()

    def get_key_record(self, key_id: str) -> sqlite3.Row | None:
        with self.lock, self.connect() as db:
            return db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()

    def get_secret(self, key_id: str) -> str:
        row = self.get_key_record(key_id)
        if row is None:
            raise KeyError("密钥不存在")
        encrypted = row["secret_encrypted"] or row["secret_dpapi"]
        if not encrypted:
            raise RuntimeError("密钥存储为空")
        return self.secret_protector.unprotect(encrypted)

    def delete_key(self, key_id: str) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))

    def set_error(self, key_id: str, error: str | None) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE api_keys SET last_error=?,updated_at=? WHERE id=?",
                (error, time.time(), key_id),
            )

    def save_snapshot(self, key_id: str, payload: dict[str, Any]) -> None:
        now = time.time()
        quota = payload.get("quota") or {}
        usage = payload.get("usage") or {}
        today = usage.get("today") or {}
        total = usage.get("total") or {}
        total_cost_raw = total.get("cost")
        try:
            total_cost = float(total_cost_raw)
        except (TypeError, ValueError):
            raise ValueError("上游未返回有效的累计用量") from None
        if not math.isfinite(total_cost) or total_cost < 0:
            raise ValueError("上游返回的累计用量无效")
        windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO usage_snapshots(
                    key_id,sampled_at,total_cost,today_cost,quota_limit,quota_used,remaining,
                    used_5h,used_1d,used_7d,today_requests,total_requests,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key_id,
                    now,
                    total_cost,
                    safe_float(today.get("cost")),
                    safe_float(quota.get("limit")),
                    safe_float(quota.get("used")),
                    safe_float(payload.get("remaining")),
                    safe_float((windows.get("5h") or {}).get("used")),
                    safe_float((windows.get("1d") or {}).get("used")),
                    safe_float((windows.get("7d") or {}).get("used")),
                    int(today.get("requests") or 0),
                    int(total.get("requests") or 0),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            for item in payload.get("daily_usage") or []:
                usage_date = str(item.get("date") or "")[:10]
                if not usage_date:
                    continue
                db.execute(
                    """INSERT INTO daily_usage(
                        key_id,usage_date,cost,requests,input_tokens,output_tokens,total_tokens
                    ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(key_id,usage_date) DO UPDATE SET
                        cost=excluded.cost,requests=excluded.requests,input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,total_tokens=excluded.total_tokens""",
                    (
                        key_id,
                        usage_date,
                        safe_float(item.get("cost")),
                        int(item.get("requests") or 0),
                        int(item.get("input_tokens") or 0),
                        int(item.get("output_tokens") or 0),
                        int(item.get("total_tokens") or 0),
                    ),
                )
            cutoff = now - RETENTION_DAYS * 86400
            cutoff_date = datetime.fromtimestamp(cutoff).date().isoformat()
            db.execute("DELETE FROM usage_snapshots WHERE sampled_at < ?", (cutoff,))
            db.execute("DELETE FROM daily_usage WHERE usage_date < ?", (cutoff_date,))
            db.execute("UPDATE api_keys SET last_error=NULL,updated_at=? WHERE id=?", (now, key_id))

    def latest_payload(self, key_id: str) -> dict[str, Any] | None:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM usage_snapshots WHERE key_id=? ORDER BY sampled_at DESC,id DESC LIMIT 1",
                (key_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    @staticmethod
    def _snapshot_interval_hint(db: sqlite3.Connection, key_id: str) -> float:
        rows = db.execute(
            """SELECT sampled_at FROM usage_snapshots WHERE key_id=?
            ORDER BY sampled_at DESC,id DESC LIMIT 6""",
            (key_id,),
        ).fetchall()
        intervals = sorted(
            later["sampled_at"] - earlier["sampled_at"]
            for later, earlier in zip(rows, rows[1:])
            if later["sampled_at"] > earlier["sampled_at"]
        )
        return intervals[len(intervals) // 2] if intervals else FOREGROUND_INTERVAL

    def rates(self, key_id: str) -> dict[str, Any]:
        now = time.time()
        with self.lock, self.connect() as db:
            latest = db.execute(
                "SELECT id,sampled_at,total_cost,today_cost FROM usage_snapshots WHERE key_id=? ORDER BY sampled_at DESC,id DESC LIMIT 1",
                (key_id,),
            ).fetchone()
            if latest is None:
                return {
                    "speed10m": None,
                    "speed1h": None,
                    "intervals": {
                        "10m": {"value": None, "status": "unrecorded", "observedSeconds": 0},
                        "1h": {"value": None, "status": "unrecorded", "observedSeconds": 0},
                    },
                    "avgMin": 0,
                    "avgHour": 0,
                    "avgDay": 0,
                    "averages": {
                        "today": {"cost": 0, "avgMin": 0, "avgHour": 0, "label": ""},
                        "week": {"cost": 0, "avgMin": 0, "avgHour": 0, "label": ""},
                    },
                    "hourly12h": [],
                    "tenMinute2h": [],
                    "timezone": "UTC+8",
                }

            def period_usage(seconds: int) -> dict[str, Any]:
                if now - latest["sampled_at"] > max(600, seconds):
                    return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                target = latest["sampled_at"] - seconds
                before = db.execute(
                    """SELECT id,sampled_at,total_cost FROM usage_snapshots
                    WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                    (key_id, target),
                ).fetchone()
                after = db.execute(
                    """SELECT id,sampled_at,total_cost FROM usage_snapshots
                    WHERE key_id=? AND sampled_at>=? AND
                    (sampled_at<? OR (sampled_at=? AND id<?))
                    ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                    (key_id, target, latest["sampled_at"], latest["sampled_at"], latest["id"]),
                ).fetchone()
                if before is None:
                    first = db.execute(
                        """SELECT id,sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND (sampled_at<? OR (sampled_at=? AND id<?))
                        ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                        (key_id, latest["sampled_at"], latest["sampled_at"], latest["id"]),
                    ).fetchone()
                    if first is None:
                        return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                    elapsed = latest["sampled_at"] - first["sampled_at"]
                    delta = latest["total_cost"] - first["total_cost"]
                    if elapsed <= 0 or delta < 0:
                        return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                    return {
                        "value": delta,
                        "status": "estimated",
                        "observedSeconds": round(elapsed),
                    }
                if after is None:
                    after = before
                span = after["sampled_at"] - before["sampled_at"]
                if span > 0:
                    ratio = (target - before["sampled_at"]) / span
                    target_cost = before["total_cost"] + (after["total_cost"] - before["total_cost"]) * ratio
                else:
                    target_cost = before["total_cost"]
                delta = latest["total_cost"] - target_cost
                if delta < 0:
                    return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                boundary_tolerance = min(
                    600,
                    max(90, self._snapshot_interval_hint(db, key_id) * 2),
                )
                covers_start = (
                    target - before["sampled_at"] <= boundary_tolerance
                    and after["sampled_at"] - target <= boundary_tolerance
                )
                return {
                    "value": delta,
                    "status": "recorded" if covers_start else "estimated",
                    "observedSeconds": seconds,
                }

            interval_10m = period_usage(600)
            interval_1h = period_usage(3600)
            now_local = datetime.fromtimestamp(now, BUSINESS_TIMEZONE)
            today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start - timedelta(days=today_start.weekday())
            rows = db.execute(
                """SELECT usage_date,cost FROM daily_usage
                WHERE key_id=? AND usage_date>=? AND usage_date<=? ORDER BY usage_date""",
                (key_id, week_start.date().isoformat(), today_start.date().isoformat()),
            ).fetchall()
            daily_costs = {row["usage_date"]: safe_float(row["cost"]) for row in rows}
            today_key = today_start.date().isoformat()
            today_cost = daily_costs.get(today_key, safe_float(latest["today_cost"]))
            daily_costs[today_key] = today_cost
            week_cost = sum(daily_costs.values())

            def usage_buckets(
                bucket_seconds: int, count: int, boundary: datetime
            ) -> list[dict[str, Any]]:
                buckets: list[dict[str, Any]] = []
                boundary_tolerance = min(
                    600,
                    max(90, self._snapshot_interval_hint(db, key_id)),
                )
                for offset in range(count, 0, -1):
                    start = boundary - timedelta(seconds=bucket_seconds * offset)
                    end = start + timedelta(seconds=bucket_seconds)
                    start_timestamp = start.timestamp()
                    end_timestamp = end.timestamp()
                    before = db.execute(
                        """SELECT sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                        (key_id, start_timestamp),
                    ).fetchone()
                    after = db.execute(
                        """SELECT sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                        (key_id, end_timestamp),
                    ).fetchone()
                    if before and start_timestamp - before["sampled_at"] > boundary_tolerance:
                        before = db.execute(
                            """SELECT sampled_at,total_cost FROM usage_snapshots
                            WHERE key_id=? AND sampled_at>? AND sampled_at<=?
                            ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                            (key_id, start_timestamp, end_timestamp),
                        ).fetchone()
                    elif before is None:
                        before = db.execute(
                            """SELECT sampled_at,total_cost FROM usage_snapshots
                            WHERE key_id=? AND sampled_at>? AND sampled_at<=?
                            ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                            (key_id, start_timestamp, end_timestamp),
                        ).fetchone()
                    if after and after["sampled_at"] <= start_timestamp:
                        after = None
                    elapsed = (
                        after["sampled_at"] - before["sampled_at"]
                        if before and after
                        else 0
                    )
                    delta = (
                        after["total_cost"] - before["total_cost"]
                        if before and after
                        else -1
                    )
                    if elapsed > 0 and delta >= 0:
                        covers_start = (
                            abs(start_timestamp - before["sampled_at"])
                            <= boundary_tolerance
                        )
                        covers_end = (
                            end_timestamp - after["sampled_at"] <= boundary_tolerance
                        )
                        status = (
                            "recorded" if covers_start and covers_end else "estimated"
                        )
                        cost = delta * bucket_seconds / elapsed
                    else:
                        status = "unrecorded"
                        cost = None
                    buckets.append(
                        {
                            "startTimestamp": int(start_timestamp * 1000),
                            "endTimestamp": int(end_timestamp * 1000),
                            "cost": cost,
                            "status": status,
                            "observedSeconds": round(max(0, elapsed)),
                        }
                    )
                return buckets

            current_hour = now_local.replace(minute=0, second=0, microsecond=0)
            current_ten_minutes = now_local.replace(
                minute=(now_local.minute // 10) * 10,
                second=0,
                microsecond=0,
            )
            hourly_usage = usage_buckets(3600, 12, current_hour)
            ten_minute_usage = usage_buckets(600, 12, current_ten_minutes)

        today_elapsed_minutes = max(1.0, (now_local - today_start).total_seconds() / 60)
        week_elapsed_minutes = max(1.0, (now_local - week_start).total_seconds() / 60)

        def period_average(cost: float, elapsed_minutes: float, label: str) -> dict[str, Any]:
            return {
                "cost": cost,
                "avgMin": cost / elapsed_minutes,
                "avgHour": cost / (elapsed_minutes / 60),
                "label": label,
            }

        today_average = period_average(
            today_cost,
            today_elapsed_minutes,
            today_start.strftime("%Y-%m-%d"),
        )
        week_average = period_average(
            week_cost,
            week_elapsed_minutes,
            f"{week_start:%m-%d} 至 {now_local:%m-%d}",
        )
        return {
            "speed10m": interval_10m["value"],
            "speed1h": interval_1h["value"],
            "intervals": {"10m": interval_10m, "1h": interval_1h},
            "avgMin": today_average["avgMin"],
            "avgHour": today_average["avgHour"],
            "avgDay": today_cost,
            "averages": {"today": today_average, "week": week_average},
            "hourly12h": hourly_usage,
            "tenMinute2h": ten_minute_usage,
            "timezone": "UTC+8",
        }

    def get_thresholds(self) -> dict[str, float]:
        defaults = {"warn": 50.0, "danger": 25.0, "critical": 10.0}
        with self.lock, self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE name='thresholds'").fetchone()
        if not row:
            return defaults
        try:
            loaded = json.loads(row["value"])
            return {key: safe_float(loaded.get(key), value) for key, value in defaults.items()}
        except (TypeError, json.JSONDecodeError):
            return defaults

    def set_thresholds(self, thresholds: dict[str, Any]) -> dict[str, float]:
        clean = {
            "warn": min(100.0, max(0.0, safe_float(thresholds.get("warn"), 50))),
            "danger": min(100.0, max(0.0, safe_float(thresholds.get("danger"), 25))),
            "critical": min(100.0, max(0.0, safe_float(thresholds.get("critical"), 10))),
        }
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO settings(name,value) VALUES('thresholds',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (json.dumps(clean),),
            )
        return clean

    def get_refresh_intervals(self) -> dict[str, int]:
        defaults = {
            "foreground": FOREGROUND_INTERVAL,
            "background": BACKGROUND_INTERVAL,
        }
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='refresh_intervals'"
            ).fetchone()
        if not row:
            return defaults
        try:
            loaded = json.loads(row["value"])
            return {
                "foreground": max(
                    FOREGROUND_INTERVAL,
                    int(safe_float(loaded.get("foreground"), FOREGROUND_INTERVAL)),
                ),
                "background": max(
                    BACKGROUND_INTERVAL,
                    int(safe_float(loaded.get("background"), BACKGROUND_INTERVAL)),
                ),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults

    def set_refresh_intervals(
        self, foreground: Any, background: Any
    ) -> dict[str, int]:
        clean = {
            "foreground": max(
                FOREGROUND_INTERVAL,
                int(safe_float(foreground, FOREGROUND_INTERVAL)),
            ),
            "background": max(
                BACKGROUND_INTERVAL,
                int(safe_float(background, BACKGROUND_INTERVAL)),
            ),
        }
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('refresh_intervals',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (json.dumps(clean),),
            )
        return clean

    def get_rate_limit_progress_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='rate_limit_progress_mode'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"remaining", "used"} else "remaining"

    def set_rate_limit_progress_mode(self, mode: Any) -> str:
        clean = "used" if str(mode).lower() == "used" else "remaining"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('rate_limit_progress_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_update_frequency(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='update_frequency'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"startup", "weekly", "manual"} else "startup"

    def set_update_frequency(self, frequency: Any) -> str:
        clean = str(frequency).lower()
        if clean not in {"startup", "weekly", "manual"}:
            clean = "startup"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('update_frequency',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_ignored_update_version(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='ignored_update_version'"
            ).fetchone()
        return str(row["value"]).strip() if row else ""

    def set_ignored_update_version(self, version: Any) -> str:
        clean = str(version or "").strip().lstrip("v")
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('ignored_update_version',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_close_action(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='close_action'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"exit", "tray", "ask"} else "ask"

    def set_close_action(self, action: Any) -> str:
        clean = str(action).lower()
        if clean not in {"exit", "tray", "ask"}:
            clean = "ask"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('close_action',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_always_on_top(self) -> bool:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='always_on_top'"
            ).fetchone()
        return bool(row and str(row["value"]).strip() == "1")

    def set_always_on_top(self, enabled: Any) -> bool:
        clean = bool(enabled)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('always_on_top',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                ("1" if clean else "0",),
            )
        return clean

    def get_window_size(self) -> dict[str, int]:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='window_size'"
            ).fetchone()
        if not row:
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        try:
            loaded = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        if not isinstance(loaded, dict):
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        return normalize_window_size(loaded.get("width"), loaded.get("height"))

    def set_window_size(self, width: Any, height: Any) -> dict[str, int]:
        clean = normalize_window_size(width, height)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('window_size',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (json.dumps(clean, separators=(",", ":")),),
            )
        return clean

    def get_background_ui_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='background_ui_mode'"
            ).fetchone()
        return normalize_background_ui_mode(row["value"] if row else None)

    def set_background_ui_mode(self, mode: Any) -> str:
        clean = normalize_background_ui_mode(mode)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('background_ui_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_title_bar_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='title_bar_mode'"
            ).fetchone()
        return normalize_title_bar_mode(row["value"] if row else None)

    def set_title_bar_mode(self, mode: Any) -> str:
        clean = normalize_title_bar_mode(mode)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('title_bar_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_last_update_check(self) -> float:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='last_update_check'"
            ).fetchone()
        return max(0.0, safe_float(row["value"])) if row else 0.0

    def set_last_update_check(self, checked_at: float) -> float:
        clean = max(0.0, safe_float(checked_at))
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('last_update_check',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (str(clean),),
            )
        return clean

    def alert_severity(self, key_id: str, metric: str) -> int:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT severity FROM alert_state WHERE key_id=? AND metric=?", (key_id, metric)
            ).fetchone()
        return int(row["severity"]) if row else 0

    def set_alert_severity(self, key_id: str, metric: str, severity: int) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO alert_state(key_id,metric,severity,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key_id,metric) DO UPDATE SET severity=excluded.severity,updated_at=excluded.updated_at""",
                (key_id, metric, severity, time.time()),
            )

    def reset_alert_metrics(self, key_id: str, metrics: set[str]) -> None:
        if not metrics:
            return
        with self.lock, self.connect() as db:
            db.executemany(
                "DELETE FROM alert_state WHERE key_id=? AND metric=?",
                ((key_id, metric) for metric in metrics),
            )

    def reset_limit_alerts(self) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM alert_state WHERE metric NOT LIKE '%负载'")
