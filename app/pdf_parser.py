import requests
import tempfile
import os
import logging
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

def extract_text_from_nse_pdf(pdf_url: str) -> str:
    """
    Downloads a PDF from NSE archives into memory/temp file, 
    extracts the text using PyPDF2, and returns it.
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
        try:
            s.get('https://www.nseindia.com', headers=headers, timeout=15)
        except Exception as landing_err:
            logger.debug(f"NSE landing page hit warning: {landing_err}")
        
        import time
        time.sleep(1.0)
        
        response = None
        try:
            r = s.get(pdf_url, headers=headers, stream=True, timeout=30)
            last_status = r.status_code
            if r.status_code == 200:
                response = r
            elif r.status_code == 404:
                logger.error(f"❌ [PDF FETCH FAILURE] HTTP 404 Not Found — PDF does not exist on NSE archives: {pdf_url}")
                return ""
            else:
                logger.warning(f"⚠️ [PDF FETCH WARN] HTTP {r.status_code} received from NSE archives for: {pdf_url}")
        except Exception as direct_err:
            logger.debug(f"Direct PDF fetch error: {direct_err}")

        if response is None:
            from pledge_scraper import get_crawlora_api_key
            crawlora_key = get_crawlora_api_key()
            if crawlora_key:
                try:
                    c_resp = requests.get('https://api.crawlora.net/v1/scrape', params={'api_key': crawlora_key, 'url': pdf_url}, stream=True, timeout=30)
                    last_status = c_resp.status_code
                    if c_resp.status_code == 200:
                        response = c_resp
                except Exception as crawlora_err:
                    logger.debug(f"Crawlora PDF fetch failed: {crawlora_err}")

        if response is None:
            logger.error(f"❌ [PDF FETCH FAILURE] Failed to fetch PDF from {pdf_url} (Last HTTP Status: {last_status}) via direct session & Crawlora")
            return ""
        
        # Write to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
            
        # Parse PDF
        text = ""
        reader = PdfReader(tmp_path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as dec_err:
                logger.warning(f"Failed to decrypt PDF with empty password: {dec_err}")
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            
        extracted_text = text.strip()
        logger.info(f"✅ [PDF EXTRACT SUCCESS] Extracted {len(extracted_text)} characters from {pdf_url}")
        return extracted_text
    except Exception as e:
        logger.error(f"❌ [PDF EXTRACT FAILURE] Failed to extract text from {pdf_url}: {e}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as remove_err:
                logger.warning(f"Failed to remove temp file {tmp_path}: {remove_err}")
