import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MOCK_CACHE_FILE = "data/fii_block_deals.json"

def setup_mock_deals():
    # Setup mock deals JSON containing FII, DII, and Promoter buying footprint entries
    mock_data = {
        "date": str(datetime.now(IST).date()),
        "version": 1,
        "deals": {
            "RELIANCE": {
                "fii": ["NOMURA"],
                "dii_super": ["HDFC MUTUAL FUND"],
                "promoter": ["RELIANCE INDUSTRIES"]
            },
            "ADANIENT": {
                "fii": [],
                "dii_super": [],
                "promoter": ["ADANI FAMILY TRUST"]
            },
            "TCS": {
                "fii": ["MORGAN STANLEY"],
                "dii_super": ["SBI MUTUAL FUND"],
                "promoter": []
            },
            "SBIN": {
                "fii": ["GOLDMAN SACHS"],
                "dii_super": [],
                "promoter": []
            }
        }
    }
    
    os.makedirs(os.path.dirname(MOCK_CACHE_FILE), exist_ok=True)
    # Save original if it exists to restore later
    backup = None
    if os.path.exists(MOCK_CACHE_FILE):
        try:
            with open(MOCK_CACHE_FILE, "r") as f:
                backup = f.read()
        except Exception:
            pass
            
    with open(MOCK_CACHE_FILE, "w") as f:
        json.dump(mock_data, f, indent=2)
        
    return backup

def restore_backup(backup):
    if backup:
        with open(MOCK_CACHE_FILE, "w") as f:
            f.write(backup)
    elif os.path.exists(MOCK_CACHE_FILE):
        os.remove(MOCK_CACHE_FILE)

def run_e2e_sanity():
    backup = setup_mock_deals()
    
    # Import scanners
    from app.block_deal_detector import compute_inst_bonus, get_inst_footprints
    
    symbols = ["RELIANCE", "ADANIENT", "TCS", "SBIN", "INFY"]
    base_scores = [85, 95, 75, 100, 80]
    
    logger.info("==========================================================================")
    logger.info("   INSTITUTIONAL FOOTPRINTS E2E SANITY CHECK RESULT")
    logger.info("==========================================================================")
    logger.info(f"{'Symbol':<12} | {'FII':<12} | {'DII/Super':<15} | {'Promoter':<18} | {'Base':<5} | {'Bonus':<5} | {'Final':<5}")
    logger.info("-" * 90)
    
    records = []
    
    for sym, base in zip(symbols, base_scores):
        footprints = get_inst_footprints(sym)
        fii = ", ".join(footprints["fii"]) if footprints["fii"] else "None"
        dii = ", ".join(footprints["dii_super"]) if footprints["dii_super"] else "None"
        prom = ", ".join(footprints["promoter"]) if footprints["promoter"] else "None"
        
        bonus = compute_inst_bonus(sym, base)
        final = base + bonus
        
        logger.info(f"{sym:<12} | {fii:<12} | {dii:<15} | {prom:<18} | {base:<5} | {bonus:<5} | {final:<5}")
        records.append({
            "Symbol": sym,
            "FII": fii,
            "DII": dii,
            "Promoter": prom,
            "Base": base,
            "Bonus": bonus,
            "Final": final
        })
        
    logger.info("==========================================================================")
    
    # Save output to CSV
    csv_file = "data/institutional_e2e_results.csv"
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    with open(csv_file, "w") as f:
        f.write("Symbol,FII,DII,Promoter,BaseScore,InstBonus,FinalScore\n")
        for r in records:
            f.write(f"{r['Symbol']},{r['FII']},{r['DII']},{r['Promoter']},{r['Base']},{r['Bonus']},{r['Final']}\n")
            
    logger.info(f"💾 Sanity results written to {csv_file}")
    
    restore_backup(backup)

if __name__ == "__main__":
    run_e2e_sanity()
