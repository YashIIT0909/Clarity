import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
TTL = timedelta(hours=int(os.getenv("SERPAPI_CACHE_TTL_HOURS", "72")))


def _hash(engine, params):
    return hashlib.sha256(json.dumps({"engine": engine, **params}, sort_keys=True).encode()).hexdigest()


def cached(engine: str, cache_key: dict | None = None, **params) -> dict | None:
    """The cached response if fresh (or pinned), else None. Never fetches."""
    cutoff = (datetime.now(timezone.utc) - TTL).strftime("%Y-%m-%dT%H:%M:%SZ")
    hit = (
        db.table("serpapi_cache").select("response_json, fetched_at")
        .eq("engine", engine).eq("query_params_hash", _hash(engine, cache_key or params))
        .or_(f"fetched_at.gte.{cutoff},pinned.is.true").limit(1).execute().data
    )
    return hit and {**hit[0]["response_json"], "_fetched_at": hit[0]["fetched_at"]}


def search(engine: str, cache_key: dict | None = None, **params) -> dict:
    """SerpApi call, read/write-through the serpapi_cache table. Pinned rows (demo fixtures) never expire.
    cache_key: identity to cache under instead of the request params (e.g. an image's sha256 rather than an
    upload's image_id, which differs per upload). Adds `_fetched_at`: when SerpApi actually returned this data."""
    if hit := cached(engine, cache_key, **params):
        return hit
    h = _hash(engine, cache_key or params)
    params = {"engine": engine, **params}

    # 120 s: slow engines (Google Forums took 78 s for Switch 2) still finish and land in the cache, even when a caller
    # (owner receipts) stopped waiting at its own shorter timeout.
    r = httpx.get("https://serpapi.com/search.json", params={**params, "api_key": os.environ["SERPAPI_KEY"]}, timeout=120)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        # "hasn't returned any results" is an answer (e.g. no forum threads about a shop), not a failure: cache it
        # as an empty response so re-runs are free and callers see zero rows instead of an exception.
        if "hasn't returned any results" not in data["error"]:
            raise RuntimeError(f"SerpApi {engine}: {data['error']}")
        data = {"no_results": data["error"], "search_parameters": data.get("search_parameters")}
    data.pop("search_metadata", None)
    now = datetime.now(timezone.utc).isoformat()
    db.table("serpapi_cache").upsert(
        {"engine": engine, "query_params_hash": h, "response_json": data, "fetched_at": now},
        on_conflict="engine,query_params_hash",
    ).execute()
    return {**data, "_fetched_at": now}


def put(engine: str, data: dict, fetched_at: str, cache_key: dict | None = None, **params):
    """Store a response fetched outside search() (e.g. a phase0 probe) under the key search() would use. Pinned."""
    data = {k: v for k, v in data.items() if k != "search_metadata" and not k.startswith("_")}
    db.table("serpapi_cache").upsert(
        {"engine": engine, "query_params_hash": _hash(engine, cache_key or params), "response_json": data,
         "fetched_at": fetched_at, "pinned": True},
        on_conflict="engine,query_params_hash",
    ).execute()


def pin(engine: str, cache_key: dict | None = None, **params):
    """Mark an already-cached response as a demo fixture: it never expires. Never fetches."""
    rows = (db.table("serpapi_cache").update({"pinned": True})
            .eq("engine", engine).eq("query_params_hash", _hash(engine, cache_key or params)).execute().data)
    if not rows:
        raise LookupError(f"not cached, can't pin: {engine} {params}")
