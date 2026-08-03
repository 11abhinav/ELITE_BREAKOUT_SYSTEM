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
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Referer': 'https://www.nseindia.com/'
    }
    
    tmp_path = None
    try:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate="chrome120")
        except ImportError:
            s = requests.Session()
        
        # Hit main page to get cookies
        s.get('https://www.nseindia.com', headers=headers, timeout=15)
        
        # Buffer to prevent WAF blocks
        import time
        time.sleep(2.5)
        
        response = None
        try:
            response = s.get(pdf_url, headers=headers, stream=True, timeout=60)
            if response.status_code != 200:
                response = None
        except Exception:
            response = None

        if response is None:
            from pledge_scraper import get_crawlora_api_key
            crawlora_key = get_crawlora_api_key()
            if crawlora_key:
                try:
                    c_resp = requests.get('https://api.crawlora.net/v1/scrape', params={'api_key': crawlora_key, 'url': pdf_url}, stream=True, timeout=60)
                    if c_resp.status_code == 200:
                        response = c_resp
                except Exception as crawlora_err:
                    logger.debug(f"Crawlora PDF fetch failed: {crawlora_err}")

        if response is None:
            raise Exception(f"Failed to fetch PDF from {pdf_url} via direct session & Crawlora")
        
        # Write to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
            
        # Parse PDF
        text = ""
        reader = PdfReader(tmp_path)
        # [VERSION: PDF_DECRYPT_PATCH_v1.0] Decrypt encrypted/digitally-signed PDFs
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as dec_err:
                logger.warning(f"Failed to decrypt PDF with empty password: {dec_err}")
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            
        return text.strip()
    except Exception as e:
        logger.exception(f"Failed to extract text from {pdf_url}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as remove_err:
                logger.warning(f"Failed to remove temp file {tmp_path}: {remove_err}")

if __name__ == "__main__":
    # Test
    url = "https://nsearchives.nseindia.com/corporate/PARAS_10062026152832_InvestormeetKotakLondon.pdf"
    print("Testing PDF Extractor...")
    text = extract_text_from_nse_pdf(url)
    print(f"Extracted {len(text)} characters.")
    print(text[:500])
