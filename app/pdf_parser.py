import requests
import tempfile
import os
import logging

logger = logging.getLogger(__name__)

def extract_text_from_nse_pdf(pdf_url: str) -> str:
    """
    Downloads a PDF from NSE archives into memory/temp file, 
    extracts the text using modern fault-tolerant pypdf with fallbacks, and returns it.
    Logs explicit success / failure status for all HTTP responses.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Referer': 'https://www.nseindia.com/'
    }
    
    tmp_path = None
    last_status = None
    try:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate="chrome120")
        except ImportError:
            s = requests.Session()
        
        # Hit main page to get cookies
        response = None
        last_status = None

        # 1. DIRECTLY TRY WITH SCRAPER API FIRST (bypasses NSE Akamai blocks and download timeouts)
        try:
            try:
                from app.pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
            except ImportError:
                from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
            scraper_key = get_scraper_api_key()
            if scraper_key:
                try:
                    s_resp = requests.get(
                        'https://api.scraperapi.com/',
                        params={'api_key': scraper_key, 'url': pdf_url, 'country_code': 'in'},
                        stream=True,
                        timeout=35
                    )
                    last_status = s_resp.status_code
                    if s_resp.status_code == 200:
                        response = s_resp
                        logger.info(f"✅ [PDF:SCRAPERAPI] Directly fetched PDF via ScraperAPI: {pdf_url}")
                    elif s_resp.status_code in (401, 403, 429):
                        mark_key_exhausted_today(scraper_key)
                except Exception as scraper_err:
                    logger.debug(f"ScraperAPI PDF fetch failed: {scraper_err}")
        except Exception as err:
            logger.debug(f"ScraperAPI import/init error: {err}")

        # 2. ONLY IF SCRAPER TOKEN NOT WORKING / FAILS, TRY WITHOUT SCRAPER
        if response is None:
            try:
                s = get_requests_session()
                try:
                    s.get('https://www.nseindia.com', headers=headers, timeout=15)
                except Exception:
                    pass
                time.sleep(1.0)
                r = s.get(pdf_url, headers=headers, stream=True, timeout=30)
                last_status = r.status_code
                if r.status_code == 200:
                    response = r
                elif r.status_code == 404:
                    logger.warning(f"⚠️ [PDF NOT FOUND] HTTP 404 Not Found — PDF does not exist on NSE archives: {pdf_url}")
                    return ""
                else:
                    logger.warning(f"⚠️ [PDF FETCH WARN] HTTP {r.status_code} received from NSE archives for: {pdf_url}")
            except Exception as direct_err:
                logger.debug(f"Direct PDF fetch error: {direct_err}")

        if response is None:
            logger.error(f"❌ [PDF FETCH FAILURE] Failed to fetch PDF from {pdf_url} (Last HTTP Status: {last_status})")
            return ""
        
        # Write to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
            
        # Parse PDF using modern pypdf first, with fallback to PyPDF2
        text = ""
        reader = None
        
        # Engine 1: pypdf with non-strict mode
        try:
            from pypdf import PdfReader as PypdfReader
            reader = PypdfReader(tmp_path, strict=False)
        except Exception as pypdf_init_err:
            logger.debug(f"pypdf init failed: {pypdf_init_err}")
            
        # Engine 2: PyPDF2 fallback
        if reader is None:
            try:
                from PyPDF2 import PdfReader as PyPDF2Reader
                reader = PyPDF2Reader(tmp_path)
            except Exception as pypdf2_init_err:
                logger.debug(f"PyPDF2 init failed: {pypdf2_init_err}")

        if reader is not None:
            if getattr(reader, 'is_encrypted', False):
                try:
                    reader.decrypt("")
                except Exception as dec_err:
                    logger.warning(f"Failed to decrypt PDF with empty password: {dec_err}")
            
            pages = getattr(reader, 'pages', [])
            for page_idx, page in enumerate(pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                except Exception as page_err:
                    logger.warning(f"Failed to extract text from page {page_idx+1} of {pdf_url}: {page_err}")
            
        extracted_text = text.strip()
        if extracted_text:
            logger.info(f"✅ [PDF EXTRACT SUCCESS] Extracted {len(extracted_text)} characters from {pdf_url}")
            return extracted_text
        else:
            logger.warning(f"⚠️ [PDF EXTRACT EMPTY] Zero text extracted from {pdf_url}")
            return ""
            
    except Exception as e:
        logger.error(f"❌ [PDF EXTRACT FAILURE] Failed to extract text from {pdf_url}: {e}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as remove_err:
                logger.warning(f"Failed to remove temp file {tmp_path}: {remove_err}")
