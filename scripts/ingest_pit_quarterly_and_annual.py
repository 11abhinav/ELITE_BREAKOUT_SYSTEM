#!/usr/bin/env python3
"""
scripts/ingest_pit_quarterly_and_annual.py
==========================================
MASTER FULL-UNIVERSE POINT-IN-TIME (PIT) INGESTION ENGINE

PROVENANCE DECLARATION:
    Source: Screener.in consolidated/standalone pages (HTML scrape).
    actual_publication_timestamp = SEBI LODR statutory deadline (conservative upper bound):
        Q1/Q2/Q3: T+45 days | Q4/Annual: T+60 days — at 23:59:59 IST
    timestamp_basis = 'LODR_STATUTORY_DEADLINE_CONSERVATIVE'
    PEAD engine MUST use conservative_availability_timestamp for PIT gating.
"""

from __future__ import annotations
import os, re, sys, json, time, sqlite3, hashlib, logging
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
logger = logging.getLogger(__name__)

REPO_ROOT           = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR            = os.path.join(REPO_ROOT, "data")
PIT_DIR             = os.path.join(DATA_DIR, "pit_fundamentals_v1")
RAW_CHECKPOINT_DIR  = os.path.join(DATA_DIR, "pit_raw_filings")
CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
PIT_DB_PATH         = os.path.join(PIT_DIR, "pit_fundamentals_v1.db")
PIT_PARQUET_PATH    = os.path.join(PIT_DIR, "pit_fundamentals_v1.parquet")
os.makedirs(PIT_DIR, exist_ok=True)
os.makedirs(RAW_CHECKPOINT_DIR, exist_ok=True)

CLIENT_USER_AGENT = "ELITE_BREAKOUT_SYSTEM/2.0 (Research PIT Ingestion)"
TIMESTAMP_BASIS   = "LODR_STATUTORY_DEADLINE_CONSERVATIVE"
BATCH_SIZE        = 50
REQUEST_DELAY_S   = 1.8
MIN_YEAR          = 2012

MONTH_MAP = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
             "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def _last_day(y, m):
    if m in [1,3,5,7,8,10,12]: return 31
    elif m in [4,6,9,11]:       return 30
    else: return 29 if (y%4==0 and (y%100!=0 or y%400==0)) else 28

def clean_num(v):
    if not v: return None
    try:
        s = re.sub(r"[^\d\.\-]","",str(v))
        return float(s) if s and s!="-" else None
    except: return None

def parse_header_date(col, is_quarterly=False):
    parts = col.strip().lower().split()
    if len(parts)!=2: return None
    mon_s, yr_s = parts[0], parts[1]
    if mon_s not in MONTH_MAP: return None
    try: year = int(yr_s)
    except: return None
    if not (2000<=year<=2030): return None
    month = MONTH_MAP[mon_s]
    p_end = date(year, month, _last_day(year, month))
    if is_quarterly:
        if   month==3:  deadline = date(year,   5,30)
        elif month==6:  deadline = date(year,   8,14)
        elif month==9:  deadline = date(year,  11,14)
        elif month==12: deadline = date(year+1, 2,14)
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
            key = re.sub(r"[^a-zA-Z0-9\s]","",tds[0]).strip().lower()
            rows[key] = tds[1:]
    return cols, rows

def _cell(rows, key, idx):
    lst = rows.get(key) or []
    return lst[idx] if idx<len(lst) else None

def make_row(sym, fid, p_end, pub_ts, cons_ts, src, stmt, rev, op, np_, eps,
             opm=None, depr=None, ocf=None, fcf=None, debt=None, eq=None,
             roce=None, roe=None, src_tag="SCREENER"):
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
        "shares_outstanding": None, "operating_cash_flow": ocf,
        "free_cash_flow": fcf, "total_debt": debt, "total_equity": eq,
        "cash_and_equivalents": None, "depreciation_amortization": depr,
        "roce": roce, "roe": roe, "operating_margin": opm,
        "net_margin": (np_/rev*100) if (rev and rev!=0 and np_ is not None) else None,
        "source_provider": "SCREENER", "raw_NII": None, "mapped_revenue": rev,
        "mapping_rule": "REVENUE_DIRECT",
    }

def fetch_and_parse_symbol(sym, sess):
    hdrs = {"User-Agent": CLIENT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.screener.in/"}
    r, used_url = None, None
    for attempt in range(1,4):
        try:
            for url in [f"https://www.screener.in/company/{sym}/consolidated/",
                        f"https://www.screener.in/company/{sym}/"]:
                r = sess.get(url, headers=hdrs, timeout=15)
                if r.status_code==200 and r.text and "profit-loss" in r.text:
                    # Check if the table actually has columns before accepting it
                    soup_test = BeautifulSoup(r.text, "html.parser")
                    q_test, _ = extract_table(soup_test, "quarters")
                    if len(q_test) > 0:
                        used_url = url
                        break
                if r.status_code==429:
                    logger.warning(f"    [429] {sym} rate limit, wait {attempt*15}s")
                    time.sleep(attempt*15)
                elif r.status_code==404:
                    return [], 0, 0
            if used_url: break
        except Exception as exc:
            logger.warning(f"    [retry {attempt}] {sym}: {exc}")
            time.sleep(attempt*5)
    if not used_url: return [], 0, 0

    soup = BeautifulSoup(r.text, "html.parser")
    src_tag = "CONSOLIDATED" if "consolidated" in used_url else "STANDALONE"
    records = []

    # QUARTERLY
    q_cols, q_rows = extract_table(soup, "quarters")
    q_cnt = 0
    for idx, col in enumerate(q_cols):
        parsed = parse_header_date(col, is_quarterly=True)
        if not parsed: continue
        yr, mo, p_end, pub_ts, cons_ts = parsed
        if yr < MIN_YEAR: continue
        fid = f"{sym}_{p_end.strftime('%Y%m%d')}_Q_v1"
        records.append(make_row(
            sym, fid, p_end, pub_ts, cons_ts,
            f"SCREENER_QUARTERLY_{src_tag}", "QUARTERLY",
            clean_num(_cell(q_rows,"sales",idx)),
            clean_num(_cell(q_rows,"operating profit",idx)),
            clean_num(_cell(q_rows,"net profit",idx)),
            clean_num(_cell(q_rows,"eps in rs",idx)),
            opm=clean_num(_cell(q_rows,"opm",idx)),
        ))
        q_cnt += 1

    # ANNUAL
    pl_cols, pl_rows = extract_table(soup,"profit-loss")
    bs_cols, bs_rows = extract_table(soup,"balance-sheet")
    cf_cols, cf_rows = extract_table(soup,"cash-flow")
    rt_cols, rt_rows = extract_table(soup,"ratios")
    ann = {}

    for idx, col in enumerate(pl_cols):
        parsed = parse_header_date(col, False)
        if not parsed: continue
        yr, mo, p_end, pub_ts, cons_ts = parsed
        if yr < MIN_YEAR: continue
        fid = f"{sym}_{p_end.strftime('%Y%m%d')}_A_v1"
        ann[p_end] = make_row(
            sym, fid, p_end, pub_ts, cons_ts,
            f"SCREENER_AUDITED_ANNUAL_{src_tag}", "ANNUAL",
            clean_num(_cell(pl_rows,"sales",idx)),
            clean_num(_cell(pl_rows,"operating profit",idx)),
            clean_num(_cell(pl_rows,"net profit",idx)),
            clean_num(_cell(pl_rows,"eps in rs",idx)),
            opm=clean_num(_cell(pl_rows,"opm",idx)),
            depr=clean_num(_cell(pl_rows,"depreciation",idx)),
        )

    for idx, col in enumerate(bs_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        eq  = (clean_num(_cell(bs_rows,"equity capital",idx)) or 0.0)
        res = (clean_num(_cell(bs_rows,"reserves",idx)) or 0.0)
        ann[p_end]["total_equity"] = (eq+res) or None
        ann[p_end]["total_debt"]   = clean_num(_cell(bs_rows,"borrowings",idx))

    for idx, col in enumerate(cf_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        ann[p_end]["operating_cash_flow"] = clean_num(_cell(cf_rows,"cash from operating activity",idx))
        ann[p_end]["free_cash_flow"]      = None

    for idx, col in enumerate(rt_cols):
        parsed = parse_header_date(col, False)
        if not parsed or parsed[2] not in ann: continue
        p_end = parsed[2]
        ann[p_end]["roce"] = clean_num(_cell(rt_rows,"roce",idx))
        ann[p_end]["roe"]  = clean_num(_cell(rt_rows,"roe",idx))

    for p_end, rec in ann.items():
        if rec["roe"] is None and rec.get("net_profit") and rec.get("total_equity") and rec["total_equity"]>0:
            rec["roe"] = round(rec["net_profit"]/rec["total_equity"]*100,2)
        if rec["roce"] is None and rec.get("operating_profit") and rec.get("total_equity"):
            cap = rec["total_equity"]+(rec.get("total_debt") or 0)
            if cap>0: rec["roce"] = round(rec["operating_profit"]/cap*100,2)

    records.extend(list(ann.values()))
    try:
        with open(os.path.join(RAW_CHECKPOINT_DIR,f"{sym}.json"),"w") as f:
            json.dump(records,f)
    except: pass
    return records, q_cnt, len(ann)

def init_db(db_path):
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
    ]: cur.execute(s)
    try: cur.execute("ALTER TABLE pit_fundamentals_v1 ADD COLUMN timestamp_basis TEXT DEFAULT 'LODR_STATUTORY_DEADLINE_CONSERVATIVE'")
    except: pass
    con.commit(); con.close()

COLS = [
    "filing_id","symbol","period_end_date","filing_date",
    "actual_publication_timestamp","conservative_availability_timestamp","timestamp_basis",
    "source","document_id","revision_number","is_original_filing","statement_type",
    "revenue","operating_profit","net_profit","eps","shares_outstanding",
    "operating_cash_flow","free_cash_flow","total_debt","total_equity",
    "cash_and_equivalents","depreciation_amortization","roce","roe",
    "operating_margin","net_margin","source_provider","raw_NII","mapped_revenue","mapping_rule",
]

def upsert_records(con, records):
    if not records: return 0
    ph  = ",".join(["?"]*len(COLS))
    sql = f"INSERT OR REPLACE INTO pit_fundamentals_v1 ({','.join(COLS)}) VALUES ({ph})"
    rows = [tuple(r.get(c) for c in COLS) for r in records]
    con.cursor().executemany(sql, rows)
    con.commit()
    return len(rows)

def sha256(path):
    h = hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def print_summary():
    logger.info("\n" + "="*70 + "\nPOST-INGESTION SUMMARY\n" + "="*70)
    try:
        con = sqlite3.connect(PIT_DB_PATH)
        df = pd.read_sql("""
            SELECT statement_type,
                COUNT(*) AS total_rows, COUNT(DISTINCT symbol) AS symbols,
                SUM(CASE WHEN eps IS NOT NULL THEN 1 ELSE 0 END) AS has_eps,
                SUM(CASE WHEN revenue IS NOT NULL THEN 1 ELSE 0 END) AS has_revenue,
                SUM(CASE WHEN operating_profit IS NOT NULL THEN 1 ELSE 0 END) AS has_op,
                MIN(period_end_date) AS earliest, MAX(period_end_date) AS latest
            FROM pit_fundamentals_v1 GROUP BY statement_type
        """, con); con.close()
        logger.info("\n" + df.to_string(index=False))
        logger.info("\nNEXT STEP → python3 scripts/certify_pit_quarterly_dataset.py")
        logger.info("DO NOT run PEAD backtest until certification passes.")
    except Exception as e: logger.error(f"Summary error: {e}")

def main():
    logger.info("="*70)
    logger.info("PEAD_FUNDAMENTAL_V1 — PIT Ingestion (Quarterly + Annual)")
    logger.info(f"Timestamp basis: {TIMESTAMP_BASIS}")
    logger.info("="*70)
    with open(CLEAN_UNIVERSE_JSON) as f:
        syms = json.load(f)["symbols"]
    logger.info(f"Universe: {len(syms)} symbols")
    init_db(PIT_DB_PATH)
    con0 = sqlite3.connect(PIT_DB_PATH)
    have_q = set(r[0] for r in con0.execute(
        "SELECT DISTINCT symbol FROM pit_fundamentals_v1 WHERE statement_type='QUARTERLY'").fetchall())
    con0.close()
    needed = [s for s in syms if s not in have_q]
    logger.info(f"Already have quarterly: {len(have_q)} | To ingest: {len(needed)}")
    if not needed:
        logger.info("Nothing to ingest."); print_summary(); return

    sess = requests.Session()
    total_q = total_a = success = fails = 0
    fail_list = []
    buf = []
    con = sqlite3.connect(PIT_DB_PATH)

    for i, sym in enumerate(needed, 1):
        try:
            recs, q_cnt, a_cnt = fetch_and_parse_symbol(sym, sess)
            if not recs:
                logger.warning(f"  [{i}/{len(needed)}] {sym:<16} NO DATA")
                fails += 1; fail_list.append(sym)
            else:
                buf.extend(recs); total_q += q_cnt; total_a += a_cnt; success += 1
                logger.info(f"  [{i}/{len(needed)}] {sym:<16} Q={q_cnt:>3} A={a_cnt:>2} rows={len(recs)}")
        except Exception as exc:
            logger.error(f"  [{i}/{len(needed)}] {sym:<16} ERROR: {exc}")
            fails += 1; fail_list.append(sym)
        if len(buf) >= BATCH_SIZE or i == len(needed):
            n = upsert_records(con, buf)
            logger.info(f"    [DB] upserted {n} rows"); buf = []
        if i < len(needed): time.sleep(REQUEST_DELAY_S)

    con.close()
    logger.info("Exporting Parquet...")
    con = sqlite3.connect(PIT_DB_PATH)
    pd.read_sql("SELECT * FROM pit_fundamentals_v1 ORDER BY symbol, period_end_date, statement_type",con
                ).to_parquet(PIT_PARQUET_PATH, index=False)
    con.close()
    db_sha = sha256(PIT_DB_PATH)
    manifest = {
        "ingestion_run_utc": datetime.utcnow().isoformat(),
        "universe_count": len(syms), "symbols_ingested": success,
        "symbols_failed": fails, "failed_symbols": fail_list,
        "total_quarterly_rows_new": total_q, "total_annual_rows_new": total_a,
        "timestamp_basis": TIMESTAMP_BASIS,
        "provenance_note": (
            "actual_publication_timestamp = LODR statutory deadline. "
            "True board-meeting dates not recoverable from Screener. "
            "PIT gating must use conservative_availability_timestamp."
        ),
        "db_sha256": db_sha,
    }
    mpath = os.path.join(PIT_DIR,"ingestion_manifest.json")
    with open(mpath,"w") as f: json.dump(manifest,f,indent=2)
    logger.info(f"Manifest: {mpath} | DB SHA256: {db_sha}")
    print_summary()

if __name__ == "__main__":
    main()
