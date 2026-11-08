"""AI 사용량 · 비용 기록과 관리자 설정 (migrations/021 · 관리자 「비용 관리」 화면).

    insert_usage(...)        AI 호출 1번 기록 (adapters/llm/usage.py 가 API 서버에서 연결)
    monthly_usage(months)    최근 N개월(한국 시간)의 달마다 비용 · 회의록 수 · 기능별 비용 (기록이 없는 달은 0)
    budget() · save_budget   월 예산(원) · 알림 기준(%) — 관리자가 저장하면 모든 관리자에게 같은 값
    fallback_rate()          환율을 받지 못했을 때 쓰는 기본 환율 (app_settings.usd_krw_rate)
"""
from __future__ import annotations

from datetime import date

from psycopg.types.json import Jsonb

from followup import settings as cfg
from followup.adapters.postgres.db import execute, query, transaction
from .common import ValidationError

FEATURES = ("summary", "evidence", "recommend", "ask")


def insert_usage(*, meeting_id: str | None, feature: str, input_tokens: int, cached_tokens: int, output_tokens: int,
                 cost_usd: float | None) -> None:
    execute("""INSERT INTO llm_usage (meeting_id, feature, input_tokens, cached_tokens, output_tokens, cost_usd)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (meeting_id, feature, input_tokens, cached_tokens, output_tokens, cost_usd))


def _month_keys(today: date, months: int) -> list[str]:
    y, m = today.year, today.month
    keys = []
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return keys[::-1]   # 오래된 달부터


def monthly_usage(today: date, months: int) -> list[dict]:
    """[{key, year, month, cost_usd, notes, unpriced, features: {기능: cost_usd}}] — 오래된 달부터, 기록이 없는 달도 0으로.

    notes: 그 달에 분석한 회의록 수 (summary 기록의 서로 다른 meeting_id)
    unpriced: 단가가 없어 비용을 비워 둔 호출 수 (화면에 「일부 비용 미반영」)
    """
    keys = _month_keys(today, months)
    rows = query("""
        SELECT to_char(created_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM') AS key, feature,
               COALESCE(sum(cost_usd), 0)::float AS cost_usd,
               count(DISTINCT meeting_id) FILTER (WHERE feature = 'summary') AS notes,
               count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced
        FROM llm_usage
        WHERE created_at >= (%s::date)::timestamp AT TIME ZONE 'Asia/Seoul'
        GROUP BY 1, 2""", (f"{keys[0]}-01",))
    out = {k: {"key": k, "year": int(k[:4]), "month": int(k[5:]), "cost_usd": 0.0, "notes": 0, "unpriced": 0,
               "features": {f: 0.0 for f in FEATURES}} for k in keys}
    for r in rows:
        mo = out.get(r["key"])
        if mo is None:
            continue
        mo["cost_usd"] += r["cost_usd"]
        mo["notes"] += r["notes"]
        mo["unpriced"] += r["unpriced"]
        mo["features"][r["feature"]] = mo["features"].get(r["feature"], 0.0) + r["cost_usd"]
    return [out[k] for k in keys]


def _settings(*keys: str) -> dict:
    rows = query("SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(keys),))
    return {r["key"]: r["value"] for r in rows}


def budget() -> dict:
    values = _settings("monthly_budget_krw", "budget_alert_pct")
    return {"amount_krw": int(values.get("monthly_budget_krw", cfg.DEFAULT_BUDGET_KRW)),
            "alert_pct": int(values.get("budget_alert_pct", cfg.DEFAULT_BUDGET_ALERT_PCT))}


def save_budget(*, actor_id: str, amount_krw: int, alert_pct: int) -> dict:
    if not isinstance(amount_krw, int) or amount_krw <= 0:
        raise ValidationError("월 예산은 0보다 큰 금액이어야 해요.")
    if not isinstance(alert_pct, int) or not 1 <= alert_pct <= 100:
        raise ValidationError("알림 기준은 1~100% 사이여야 해요.")
    with transaction() as cur:
        for key, value in (("monthly_budget_krw", amount_krw), ("budget_alert_pct", alert_pct)):
            cur.execute("""INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (%s, %s, now(), %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
                                                           updated_by = EXCLUDED.updated_by""",
                        (key, Jsonb(value), actor_id))
    return {"amount_krw": amount_krw, "alert_pct": alert_pct}


def fallback_rate() -> float:
    value = _settings("usd_krw_rate").get("usd_krw_rate")
    return float(value) if isinstance(value, (int, float)) and value > 0 else float(cfg.DEFAULT_USD_KRW)
