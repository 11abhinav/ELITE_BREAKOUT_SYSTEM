import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

# Prompt for the LLM
SYSTEM_PROMPT = """You are an expert financial equity research analyst.
You will be provided with the text extracted from a company's earnings concall transcripts or investor presentations.
You may receive TWO transcripts separated by "--- LATEST QUARTER ---" and "--- PREVIOUS QUARTER ---". 
Your job is to read them carefully and extract specific forward-looking guidance and deep fundamental commentary.
Provide highly detailed, analytical summaries for each field. Extract as much quantitative data (margins, revenue targets, capex numbers, timeline) as possible. Do not limit sentence length; provide thorough, research-grade context.
If a specific topic is not discussed in the text, return exactly the string "Not Mentioned". DO NOT hallucinate.

For the 'management_confidence' score: Be HIGHLY critical. Start at a baseline of 5. Add points ONLY for explicit upward guidance, record margins, or major debt reduction. Subtract points for headwinds, margin pressure, or missed targets. Do not default to 8. A score of 8, 9, or 10 must be exceptionally rare and reserved ONLY for massive, undeniable growth guidance.

Return the result as a strict JSON object with EXACTLY these keys:
{
    "management_confidence": (integer 1-10, be highly critical, do not default to 8),
    "guidance_delta": (string summary comparing the explicit numeric guidance given in the latest quarter vs the previous quarter. Explicitly highlight if management upgraded or downgraded their outlook. If no previous quarter text is provided, summarize any changes from previous expectations mentioned),
    "top_line_guidance": (string summary of explicit revenue or volume guidance),
    "bottom_line_guidance": (string summary of EBITDA, net profit, or margin expansion/contraction guidance),
    "demand_environment": (string summary of broader industry tailwinds, market share gains, or macro demand shifts),
    "volume_vs_pricing": (string summary of whether growth is driven by volume expansion or pricing realization),
    "capex_and_launches": (string summary of major capital expenditures, R&D, or new product pipelines),
    "working_capital_debt": (string summary of inventory levels, cash flow efficiency, or debt reduction plans),
    "key_risks": (array of strings, listing top 1-3 risks/headwinds mentioned)
}"""

def _try_gemini_model(model_name: str, gemini_key: str, text: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\nTRANSCRIPT TEXT:\n" + text}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    res = requests.post(url, json=payload, timeout=90)
    if res.status_code == 200:
        data = res.json()
        try:
            content_str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if content_str.startswith("```json"):
                content_str = content_str[7:]
            if content_str.startswith("```"):
                content_str = content_str[3:]
            if content_str.endswith("```"):
                content_str = content_str[:-3]
            content_str = content_str.strip()
            result = json.loads(content_str)
            result["model_used"] = model_name
            return result
        except Exception as e:
            raise Exception(f"Failed to parse response: {e}")
    else:
        raise Exception(f"API Error ({res.status_code}): {res.text}")



def analyze_concall_text(text: str) -> dict:
    """
    Feeds the extracted transcript text to an LLM to generate the structured JSON.
    Implements a robust fallback chain starting with the best Pro models.
    """
    if not text or len(text) < 100:
        return {"error": "Text too short or empty."}

    # Truncate text to avoid massive token limits (keep first 80k chars roughly)
    if len(text) > 80000:
        text = text[:80000]

    errors = []
    openai_key = os.getenv("OPENAI_API_KEY")

    from gemini_key_manager import get_active_gemini_key, mark_gemini_key_exhausted

    # Fallback Chain 1: Gemini Models with Sticky Active Key Selection
    gemini_key = get_active_gemini_key()
    if gemini_key:
        gemini_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ]
        
        for model in gemini_models:
            curr_key = get_active_gemini_key()
            if not curr_key:
                logger.warning("🚨 All Gemini keys are currently blacklisted for 7 days!")
                break
                
            masked_key = f"{curr_key[:4]}...{curr_key[-4:]}" if len(curr_key) > 8 else "GEMINI_KEY"
            try:
                logger.info(f"Attempting AI analysis with {model} (Key: [{masked_key}])...")
                result = _try_gemini_model(model, curr_key, text)
                result["key_used"] = masked_key
                from data_fetch_status import mark_success
                mark_success('gemini')
                return result
            except Exception as e:
                err_str = str(e).replace(curr_key, "[REDACTED_KEY]")
                if "429" in err_str or "Quota" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"❌ [GEMINI QUOTA EXHAUSTED] {model} hit rate/quota limit on key [{masked_key}]. Blacklisting key for 7 days...")
                    errors.append(f"{model} Rate Limited (Key: [{masked_key}])")
                    
                    from data_fetch_status import mark_failure
                    mark_failure('gemini', f"{model} Rate Limited (Key [{masked_key}])")
                    mark_gemini_key_exhausted(curr_key, f"Quota Exceeded on {model}")
                    
                    # Try immediately with next non-blacklisted active key
                    next_key = get_active_gemini_key()
                    if next_key:
                        next_masked = f"{next_key[:4]}...{next_key[-4:]}" if len(next_key) > 8 else "NEXT_KEY"
                        logger.info(f"🔄 Switched to next available Gemini key [{next_masked}]. Retrying model {model}...")
                        try:
                            result = _try_gemini_model(model, next_key, text)
                            result["key_used"] = next_masked
                            from data_fetch_status import mark_success
                            mark_success('gemini')
                            return result
                        except Exception as retry_err:
                            retry_err_str = str(retry_err).replace(next_key, "[REDACTED_KEY]")
                            if "429" in retry_err_str or "Quota" in retry_err_str or "RESOURCE_EXHAUSTED" in retry_err_str:
                                mark_gemini_key_exhausted(next_key, f"Quota Exceeded on retry {model}")
                            logger.warning(f"Retry on model {model} failed: {retry_err_str}")
                    else:
                        logger.warning("🚨 All Gemini keys exhausted. Proceeding to fallback chain...")
                        break
                elif "404" in err_str or "NOT_FOUND" in err_str:
                    logger.warning(f"Skipping {model} as it is not found on key [{masked_key}].")
                    continue
                else:
                    logger.warning(f"{model} failed: {err_str}")
                    errors.append(f"{model}: {err_str}")
                    from data_fetch_status import mark_failure
                    mark_failure('gemini', f"{model}: {err_str}")
                    continue

    from data_fetch_status import mark_failure
    final_error = errors[-1] if errors else "All AI models failed or all Gemini keys are 7-day blacklisted."
    mark_failure('gemini', final_error)
    return {"error": "All AI models in the fallback chain failed.", "details": errors}
