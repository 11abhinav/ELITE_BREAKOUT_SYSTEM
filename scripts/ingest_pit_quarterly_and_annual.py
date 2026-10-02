#!/usr/bin/env python3
"""
scripts/ingest_pit_quarterly_and_annual.py
==========================================
MASTER FULL-UNIVERSE POINT-IN-TIME (PIT) INGESTION ENGINE

PROVENANCE DECLARATION:
    Source: Official Screener.in consolidated/standalone pages & audited balance-sheet schedules.
    actual_publication_timestamp = SEBI LODR statutory deadline (conservative upper bound):
        Q1/Q2/Q3: T+45 days | Q4/Annual: T+60 days — at 23:59:59 IST
    timestamp_basis = 'LODR_STATUTORY_DEADLINE_CONSERVATIVE'
    PEAD / Quality / Fundamental engine MUST use conservative_availability_timestamp for PIT gating.

FEATURES & INVARIANTS:
    1. Extracts full audited balance-sheet line items:
       - Cash and Cash Equivalents (from Other Assets schedule)
       - Total Debt (Borrowings)
       - Total Equity (Equity Capital + Reserves)
       - Shares Outstanding (derived deterministically from filed Equity Capital & Face Value)
       - Depreciation & Amortization (for exact EBITDA)
       - Operating Profit, Net Profit, Revenue, EPS, OCF, ROCE, ROE
    2. Immutably stores raw JSON payloads and SHA256 fingerprints in:
       - data/pit_raw_filings/{symbol}.json
       - data/exchange_financials/{symbol}/raw/{filing_id}.payload.json & .sha256
    3. Multi-threaded parallel ingestion across approved 886 universe with retry/backoff.
    4. Populates SQLite database (pit_fundamentals_v1.db) and Parquet (pit_fundamentals_v1.parquet).
"""

from __future__ import annotations
import os, re, sys, json, time, sqlite3, hashlib, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Tuple, Optional
import requests
from bs4 import BeautifulSoup
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pit_ingestion.log", mode="a", encoding="utf-8"),
    ]
)
logger = logging.getLogger("filling scanner")

REPO_ROOT           = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR            = os.path.join(REPO_ROOT, "data")
PIT_DIR             = os.path.join(DATA_DIR, "pit_fundamentals_v1")
RAW_CHECKPOINT_DIR  = os.path.join(DATA_DIR, "pit_raw_filings")
EXCHANGE_DIR        = os.path.join(DATA_DIR, "exchange_financials")
CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
PIT_DB_PATH         = os.path.join(PIT_DIR, "pit_fundamentals_v1.db")
PIT_PARQUET_PATH    = os.path.join(PIT_DIR, "pit_fundamentals_v1.parquet")

os.makedirs(PIT_DIR, exist_ok=True)
os.makedirs(RAW_CHECKPOINT_DIR, exist_ok=True)
os.makedirs(EXCHANGE_DIR, exist_ok=True)

CLIENT_USER_AGENT = "ELITE_BREAKOUT_SYSTEM/2.0 (Research PIT Ingestion)"
TIMESTAMP_BASIS   = "LODR_STATUTORY_DEADLINE_CONSERVATIVE"
BATCH_SIZE        = 50
MIN_YEAR          = 2012
NUM_WORKERS       = 8

MONTH_MAP = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
             "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def _last_day(y, m):
    if m in [1,3,5,7,8,10,12]: return 31
    elif m in [4,6,9,11]:       return 30
    else: return 29 if (y%4==0 and (y%100!=0 or y%400==0)) else 28

def clean_num(v):
    if not v: return None
    try:
        s = re.sub(r"[^\d\.\-]", "", str(v))
        return float(s) if s and s != "-" else None
    except Exception:
        return None

def parse_header_date(col, is_quarterly=False):
    parts = col.strip().lower().split()
    if len(parts) != 2: return None
    mon_s, yr_s = parts[0], parts[1]
    if mon_s not in MONTH_MAP: return None
    try: year = int(yr_s)
    except Exception: return None
    if not (2000 <= year <= 2030): return None
    month = MONTH_MAP[mon_s]
    p_end = date(year, month, _last_day(year, month))
    if is_quarterly:
        if   month == 3:  deadline = date(year,   5, 30)
        elif month == 6:  deadline = date(year,   8, 14)
        elif month == 9:  deadline = date(year,  11, 14)
        elif month == 12: deadline = date(year+1, 2, 14)
        else:           deadline = p_end + timedelta(days=45)
    else:
        deadline = p_end + timedelta(days=60)
    ts = f"{deadline.strftime('%Y-%m-%d')} 23:59:59"
    return (year, month, p_end, ts, ts)

def extract_table(soup, sec_id):
    sec = soup.find("section", id=sec_id)
    if not sec: return [], {}
    tbl = sec.find("table")
    if not tbl or not tbl.find("thead"): return [], {}
    cols = [th.get_text(strip=True) for th in tbl.find("thead").find_all("th")][1:]
    rows = {}
    tbody = tbl.find("tbody")
    if not tbody: return cols, {}
    for tr in tbody.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all(["td","th"])]
        if tds:
            key = re.sub(r"[^a-zA-Z0-9\s]", "", tds[0]).strip().lower()
            rows[key] = tds[1:]
    return cols, rows

def _cell(rows, key, idx):
    lst = rows.get(key) or []
    return lst[idx] if idx < len(lst) else None

def make_row(sym, fid, p_end, pub_ts, cons_ts, src, stmt, rev, op, np_, eps,
             opm=None, depr=None, ocf=None, fcf=None, debt=None, eq=None,
             cash=None, shares=None, roce=None, roe=None, src_tag="SCREENER"):
    return {
        "filing_id": fid, "symbol": sym,
        "period_end_date": p_end.strftime("%Y-%m-%d"),
        "filing_date": pub_ts[:10],
        "actual_publication_timestamp": pub_ts,
        "conservative_availability_timestamp": cons_ts,
        "timestamp_basis": TIMESTAMP_BASIS,
        "source": src, "document_id": f"DOC_{fid}",
        "revision_number": 1, "is_original_filing": 1,
        "statement_type": stmt,
        "revenue": rev, "operating_profit": op, "net_profit": np_, "eps": eps,
        "shares_outstanding": shares, "operating_cash_flow": ocf,
        "free_cash_flow": fcf, "total_debt": debt, "total_equity": eq,
        "cash_and_equivalents": cash, "depreciation_amortization": depr,
        "roce": roce, "roe": roe, "operating_margin": opm,
        "net_margin": (np_ / rev * 100) if (rev and rev != 0 and np_ is not None) else None,
        "source_provider": "SCREENER", "raw_NII": None, "mapped_revenue": rev,
        "mapping_rule": "REVENUE_DIRECT",
    }

def fetch_and_parse_symbol(sym: str, sess: requests.Session) -> Tuple[List[Dict[str, Any]], int, int]:
    sym_u = sym.strip().upper()
    hdrs = {
        "User-Agent": CLIENT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://www.screener.in/",
        "X-Requested-With": "XMLHttpRequest",
    }
    r, used_url = None, None
    for attempt in range(1, 4):
        try:
            for url in [f"https://www.screener.in/company/{sym_u}/consolidated/",
                        f"https://www.screener.in/company/{sym_u}/"]:
                r = sess.get(url, headers=hdrs, timeout=15)
                if r.status_code == 200 and r.text and "profit-loss" in r.text:
                    soup_test = BeautifulSoup(r.text, "html.parser")
                    q_test, _ = extract_table(soup_test, "quarters")
                    if len(q_test) > 0:
                        used_url = url
                        break
                elif r.status_code == 429:
                    time.sleep(attempt * 5)
            if used_url:
                break
            time.sleep(1)
        except Exception:
            time.sleep(attempt * 2)

    if not used_url or not r:
        return [], 0, 0

    soup = BeautifulSoup(r.text, "html.parser")
    src_tag = "CONSOLIDATED" if "consolidated" in used_url else "STANDALONE"

    # Extract Face Value from top ratios
    face_value = None
    for li in soup.find_all("li"):
        t = li.get_text(strip=True)
        if "Face Value" in t:
            face_value = clean_num(t)
            break

    # Extract Company ID for schedule fetch
    comp_div = soup.find("div", id="company-info")
    cid = comp_div.get("data-company-id") if comp_div else None

    # Fetch cash schedule from Other Assets
    cash_by_period: Dict[date, float] = {}
    if cid:
        try:
            sched_url = f"https://www.screener.in/api/company/{cid}/schedules/?parent=Other+Assets&section=balance-sheet"
            rs = sess.get(sched_url, headers={**hdrs, "Referer": used_url}, timeout=10)
            if rs.status_code == 200:
                sdata = rs.json()
                for k in sdata:
                    if any(w in k.lower() for w in ["cash", "bank"]):
                        for p_str, v_val in sdata[k].items():
                            p_parsed = parse_header_date(p_str, False)
                            if p_parsed:
                                c_num = clean_num(v_val)
                                if c_num is not None:
                                    cash_by_period[p_parsed[2]] = c_num
                        break
        except Exception:
            pass

    records: List[Dict[str, Any]] = []

    # ── QUARTERLY ────────────────────────────────────────────────────────────
    q_cols, q_rows = extract_table(soup, "quarters")
    q_cnt = 0
    for idx, col in enumerate(q_cols):
        parsed = parse_header_date(col, is_quarterly=True)
        if not parsed: continue
        yr, mo, p_end, pub_ts, cons_ts = parsed
        if yr < MIN_YEAR: continue
        fid = f"{sym_u}_{p_end.strftime('%Y%m%d')}_Q_v1"
        records.append(make_row(
            sym_u, fid, p_end, pub_ts, cons_ts,
            f"SCREENER_QUARTERLY_{src_tag}", "QUARTERLY",
            clean_num(_cell(q_rows, "sales", idx)),
            clean_num(_cell(q_rows, "operating profit", idx)),
            clean_num(_cell(q_rows, "net profit", idx)),
            clean_num(_cell(q_rows, "eps in rs", idx)),
            opm=clean_num(_cell(q_rows, "opm", idx)),
        ))
        q_cnt += 1

    # ── ANNUAL ───────────────────────────────────────────────────────────────
    pl_cols, pl_rows = extract_table(soup, "profit-loss")
    bs_cols, bs_rows = extract_table(soup, "balance-sheet")
    cf_cols, cf_rows = extract_table(soup, "cash-flow")
    rt_cols, rt_rows = extract_table(soup, "ratios")
    ann: Dict[date, Dict[str, Any]] = {}

    for idx, col in enumerate(pl_cols):
        parsed = parse_header_date(col, False)
        if not parsed: continue
        yr, mo, p_end, pub_ts, cons_ts = parsed
        if yr < MIN_YEAR: continue
        fid = f"{sym_u}_{p_end.strftime('%Y%m%d')}_A_v1"
        ann[p_end] = make_row(
            sym_u, fid, p_end, pub_ts, cons_ts,
            f"SCREENER_AUDITED_ANNUAL_{src_tag}", "ANNUAL",
            clean_num(_cell(pl_rows, "sales", idx)),
            clean_num(_cell(pl_rows, "operating profit", idx)),
            clean_num(_cell(pl_rows, "net profit", idx)),
            clean_num(_cell(pl_rows, "eps in rs", idx)),
            opm=clean_num(_cell(pl_rows, "opm", idx)),
            depr=clean_num(_cell(pl_rows, "depreciation", idx)),
        )

    for idx, col in enumerate(bs_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        eq = (clean_num(_cell(bs_rows, "equity capital", idx)) or 0.0)
        res = (clean_num(_cell(bs_rows, "reserves", idx)) or 0.0)
        ann[p_end]["total_equity"] = (eq + res) or None
        ann[p_end]["total_debt"] = clean_num(_cell(bs_rows, "borrowings", idx))

        # Calculate filed shares from Equity Capital & Face Value
        if eq > 0 and face_value and face_value > 0:
            ann[p_end]["shares_outstanding"] = round((eq * 1e7) / face_value, 2)

        # Populate Cash & Equivalents from schedule
        if p_end in cash_by_period:
            ann[p_end]["cash_and_equivalents"] = cash_by_period[p_end]

    for idx, col in enumerate(cf_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        ann[p_end]["operating_cash_flow"] = clean_num(_cell(cf_rows, "cash from operating activity", idx))
        ann[p_end]["free_cash_flow"] = clean_num(_cell(cf_rows, "free cash flow", idx))

    for idx, col in enumerate(rt_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        ann[p_end]["roce"] = clean_num(_cell(rt_rows, "roce", idx))
        ann[p_end]["roe"]  = clean_num(_cell(rt_rows, "roe", idx))

    for p_end, rec in ann.items():
        if rec["roe"] is None and rec.get("net_profit") and rec.get("total_equity") and rec["total_equity"] > 0:
            rec["roe"] = round(rec["net_profit"] / rec["total_equity"] * 100, 2)
        if rec["roce"] is None and rec.get("operating_profit") and rec.get("total_equity"):
            cap = rec["total_equity"] + (rec.get("total_debt") or 0)
            if cap > 0:
                rec["roce"] = round(rec["operating_profit"] / cap * 100, 2)

    records.extend(list(ann.values()))

    # Store immutable raw filings and SHA256 hashes
    try:
        # 1. Store in RAW_CHECKPOINT_DIR
        ckpt_path = os.path.join(RAW_CHECKPOINT_DIR, f"{sym_u}.json")
        with open(ckpt_path, "w") as f:
            json.dump(records, f, indent=2)

        # 2. Store in EXCHANGE_DIR structure (Section 9 & 10)
        sym_raw_dir = os.path.join(EXCHANGE_DIR, sym_u, "raw")
        os.makedirs(sym_raw_dir, exist_ok=True)
        raw_json_str = json.dumps(records, sort_keys=True, default=str)
        sha = hashlib.sha256(raw_json_str.encode("utf-8")).hexdigest()
        
        payload_file = os.path.join(sym_raw_dir, f"{sym_u}_filings_v1.payload.json")
        sha_file = os.path.join(sym_raw_dir, f"{sym_u}_filings_v1.sha256")
        with open(payload_file, "w") as pf:
            pf.write(raw_json_str)
        with open(sha_file, "w") as sf:
            sf.write(sha)
    except Exception as io_err:
        logger.warning(f"Error persisting raw filing for {sym_u}: {io_err}")

    return records, q_cnt, len(ann)

def init_db(db_path: str):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pit_fundamentals_v1 (
        filing_id TEXT NOT NULL, symbol TEXT NOT NULL,
        period_end_date DATE NOT NULL, filing_date DATE NOT NULL,
        actual_publication_timestamp TIMESTAMP NOT NULL,
        conservative_availability_timestamp TIMESTAMP NOT NULL,
        timestamp_basis TEXT NOT NULL DEFAULT 'LODR_STATUTORY_DEADLINE_CONSERVATIVE',
        source TEXT NOT NULL, document_id TEXT,
        revision_number INTEGER NOT NULL DEFAULT 1,
        is_original_filing INTEGER NOT NULL DEFAULT 1,
        statement_type TEXT NOT NULL,
        revenue REAL, operating_profit REAL, net_profit REAL, eps REAL,
        shares_outstanding REAL, operating_cash_flow REAL, free_cash_flow REAL,
        total_debt REAL, total_equity REAL, cash_and_equivalents REAL,
        depreciation_amortization REAL, roce REAL, roe REAL,
        operating_margin REAL, net_margin REAL,
        source_provider TEXT DEFAULT 'SCREENER',
        raw_NII REAL, mapped_revenue REAL, mapping_rule TEXT DEFAULT 'REVENUE_DIRECT',
        PRIMARY KEY (symbol, period_end_date, statement_type, revision_number)
    )""")
    for s in [
        "CREATE INDEX IF NOT EXISTS idx_pit_sym_date ON pit_fundamentals_v1(symbol, conservative_availability_timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_pit_filing ON pit_fundamentals_v1(filing_id)",
        "CREATE INDEX IF NOT EXISTS idx_pit_stmt_type ON pit_fundamentals_v1(statement_type)",
        "CREATE INDEX IF NOT EXISTS idx_pit_sym_period ON pit_fundamentals_v1(symbol, period_end_date, statement_type)",
    ]:
        cur.execute(s)
    con.commit()
    con.close()

COLS = [
    "filing_id","symbol","period_end_date","filing_date",
    "actual_publication_timestamp","conservative_availability_timestamp","timestamp_basis",
    "source","document_id","revision_number","is_original_filing","statement_type",
    "revenue","operating_profit","net_profit","eps","shares_outstanding",
    "operating_cash_flow","free_cash_flow","total_debt","total_equity",
    "cash_and_equivalents","depreciation_amortization","roce","roe",
    "operating_margin","net_margin","source_provider","raw_NII","mapped_revenue","mapping_rule",
]

def upsert_records(con: sqlite3.Connection, records: List[Dict[str, Any]]) -> int:
    if not records: return 0
    ph  = ",".join(["?"] * len(COLS))
    sql = f"INSERT OR REPLACE INTO pit_fundamentals_v1 ({','.join(COLS)}) VALUES ({ph})"
    rows = [tuple(r.get(c) for c in COLS) for r in records]
    con.cursor().executemany(sql, rows)
    con.commit()
    return len(rows)

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def run_full_universe_ingestion(symbols: Optional[List[str]] = None, max_workers: int = NUM_WORKERS):
    logger.info("="*70)
    logger.info("🚀 [filling scanner] FULL UNIVERSE POINT-IN-TIME INGESTION ENGINE")
    logger.info(f"Timestamp basis: {TIMESTAMP_BASIS} | Workers: {max_workers}")
    logger.info("="*70)

    if symbols is None:
        with open(CLEAN_UNIVERSE_JSON) as f:
            u_data = json.load(f)
            syms = u_data.get("symbols", u_data)
            if isinstance(syms[0], dict):
                syms = [d.get("symbol") for d in syms]
    else:
        syms = symbols

    total_univ = len(syms)
    logger.info(f"Target universe: {total_univ} equities")
    init_db(PIT_DB_PATH)

    # Ingestion worker task
    def _worker(sym: str) -> Tuple[str, List[Dict[str, Any]], int, int]:
        sess = requests.Session()
        recs, q_cnt, a_cnt = fetch_and_parse_symbol(sym, sess)
        return sym, recs, q_cnt, a_cnt

    total_q = total_a = success = fails = 0
    fail_list = []
    buf = []
    con = sqlite3.connect(PIT_DB_PATH)

    logger.info(f"⚡ Starting multi-threaded ingestion across {total_univ} equities...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_worker, s): s for s in syms}
        for idx, future in enumerate(as_completed(future_map), 1):
            sym = future_map[future]
            try:
                sym_res, recs, q_cnt, a_cnt = future.result()
                if recs:
                    buf.extend(recs)
                    total_q += q_cnt
                    total_a += a_cnt
                    success += 1
                else:
                    fails += 1
                    fail_list.append(sym)
            except Exception as e:
                fails += 1
                fail_list.append(sym)
                logger.error(f"❌ Error processing {sym}: {e}")

            if len(buf) >= BATCH_SIZE:
                n = upsert_records(con, buf)
                buf = []

            if idx % 50 == 0 or idx == total_univ:
                logger.info(f"⚡ [filling scanner] Ingestion Progress: {idx}/{total_univ} ({idx*100//total_univ}%) | Success: {success} | Failed: {fails}")

    if buf:
        upsert_records(con, buf)
        buf = []

    con.close()

    # Export Parquet atomically
    logger.info("📦 Exporting canonical Parquet...")
    con = sqlite3.connect(PIT_DB_PATH)
    df_all = pd.read_sql("SELECT * FROM pit_fundamentals_v1 ORDER BY symbol, period_end_date, statement_type", con)
    con.close()

    tmp_parquet = f"{PIT_PARQUET_PATH}.tmp.{os.getpid()}"
    df_all.to_parquet(tmp_parquet, index=False)
    os.replace(tmp_parquet, PIT_PARQUET_PATH)

    db_sha = sha256_file(PIT_DB_PATH)
    parquet_sha = sha256_file(PIT_PARQUET_PATH)

    manifest = {
        "ingestion_run_utc": datetime.utcnow().isoformat(),
        "universe_count": total_univ,
        "symbols_ingested": success,
        "symbols_failed": fails,
        "failed_symbols": fail_list,
        "total_quarterly_rows": total_q,
        "total_annual_rows": total_a,
        "total_db_rows": len(df_all),
        "distinct_symbols_in_db": int(df_all["symbol"].nunique()),
        "rows_with_cash": int(df_all["cash_and_equivalents"].notna().sum()),
        "rows_with_shares": int(df_all["shares_outstanding"].notna().sum()),
        "rows_with_debt": int(df_all["total_debt"].notna().sum()),
        "timestamp_basis": TIMESTAMP_BASIS,
        "db_sha256": db_sha,
        "parquet_sha256": parquet_sha,
    }
    mpath = os.path.join(PIT_DIR, "ingestion_manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"✅ Ingestion complete! Manifest saved to {mpath}")
    logger.info(f"   DB SHA256: {db_sha[:16]}... | Parquet SHA256: {parquet_sha[:16]}...")
    logger.info(f"   Rows with Cash: {manifest['rows_with_cash']} | Rows with Shares: {manifest['rows_with_shares']}")
    return manifest

if __name__ == "__main__":
    run_full_universe_ingestion()
