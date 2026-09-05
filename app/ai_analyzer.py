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

# [VERSION: AI_ANALYZER_V2.0]
# RULE 67 Technical Rationale:
# 1. Added 'x-goog-api-key' request header to _try_gemini_model and _discover_supported_models.
#    Google's 2025/2026 Authorization (Auth) Keys (prefix 'AQ.', e.g. AQ.A...IGcg) require this HTTP header.
#    Passing keys only as query param '?key=' caused Google to reject requests with 404/NOT_FOUND.
# 2. Implemented dynamic model discovery (_discover_supported_models) querying Google's /v1beta/models
#    to auto-detect active generateContent models for the provided key.
# 3. Updated default model cascade to prioritize active models (gemini-2.0-flash, gemini-2.0-flash-lite, etc.).
# 4. Logged explicit err_str on 404/NOT_FOUND to prevent silent error suppression.
# 5. Implemented secondary fallback to OpenAI (_try_openai_model) using gpt-4o-mini when OPENAI_API_KEY is present.

_discovered_models_cache = {}

def _discover_supported_models(gemini_key: str) -> list:
    """
    [RULE 67: DYNAMIC GEMINI MODEL DISCOVERY]
    Queries Google Generative Language API directly to retrieve all models enabled
    for this specific API key. This avoids hardcoded model guessing and immediately
    adapts when new model versions (e.g. 2.0, 2.5, 3.x) are enabled on the account.
    """
    if not gemini_key:
        return []
    if gemini_key in _discovered_models_cache:
        return _discovered_models_cache[gemini_key]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
        headers = {
            "x-goog-api-key": gemini_key,
            "Content-Type": "application/json"
        }
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            data = res.json()
            models = []
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    name = m.get("name", "")
                    clean_name = name.replace("models/", "").strip()
                    if clean_name:
                        models.append(clean_name)
            if models:
                # Rank priority: fast/flash frontier models first, followed by pro models
                def _priority(m_name: str) -> int:
                    m = m_name.lower()
                    if "2.5-flash" in m: return 1
                    if "2.0-flash" in m and "lite" not in m: return 2
                    if "2.0-flash-lite" in m: return 3
                    if "1.5-flash" in m and "8b" not in m: return 4
                    if "1.5-pro" in m: return 5
                    if "flash" in m: return 6
                    if "pro" in m: return 7
                    return 20
                models.sort(key=_priority)
                _discovered_models_cache[gemini_key] = models
                logger.info(f"✨ [GEMINI DISCOVERY] Discovered {len(models)} valid models on key. Active top chain: {models[:4]}")
                return models
        else:
            logger.debug(f"Gemini model discovery returned HTTP {res.status_code}: {res.text[:120]}")
    except Exception as disc_err:
        logger.debug(f"Gemini model discovery failed: {disc_err}")
    return []

def _try_gemini_model(model_name: str, gemini_key: str, text: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": gemini_key
    }
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\nTRANSCRIPT TEXT:\n" + text}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    res = requests.post(url, headers=headers, json=payload, timeout=90)
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


def _try_openai_model(openai_key: str, text: str) -> dict:
    """
    [RULE 67: OPENAI FALLBACK HANDLER]
    Executes concall text analysis via OpenAI's gpt-4o-mini when all Gemini keys/models
    are exhausted, blacklisted, or unavailable. Enforces strict JSON return contract.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {openai_key.strip()}"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "TRANSCRIPT TEXT:\n" + text}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2
    }
    res = requests.post(url, headers=headers, json=payload, timeout=90)
    if res.status_code == 200:
        data = res.json()
        content_str = data["choices"][0]["message"]["content"].strip()
        result = json.loads(content_str)
        result["model_used"] = "gpt-4o-mini"
        result["key_used"] = f"{openai_key[:4]}...{openai_key[-4:]}" if len(openai_key) > 8 else "OPENAI_KEY"
        return result
    else:
        raise Exception(f"OpenAI API Error ({res.status_code}): {res.text}")


def analyze_concall_text(text: str) -> dict:
    """
    Feeds the extracted transcript text to an LLM to generate the structured JSON.
    Implements a robust fallback chain starting with the best Gemini models,
    falling back to OpenAI if configured.
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
        # [RULE 67: DYNAMIC MODEL DISCOVERY WITH RESILIENT FALLBACK]
        discovered = _discover_supported_models(gemini_key)
        gemini_models = discovered if discovered else [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite"
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
                    # [RULE 67: EXPLICIT ERROR OBSERVABILITY]
                    # Log the exact error string returned by Google rather than swallowing it,
                    # so diagnostic logs reveal the exact reason for model rejection.
                    logger.warning(f"Skipping {model} on key [{masked_key}] due to 404/NOT_FOUND: {err_str}")
                    continue
                else:
                    logger.warning(f"{model} failed: {err_str}")
                    errors.append(f"{model}: {err_str}")
                    from data_fetch_status import mark_failure
                    mark_failure('gemini', f"{model}: {err_str}")
                    continue

    # Fallback Chain 2: OpenAI Models (gpt-4o-mini)
    # [RULE 67: MULTI-PROVIDER RESILIENCE]
    # If all Gemini models/keys fail or no Gemini key is provided, gracefully failover to OpenAI.
    if openai_key:
        masked_openai = f"{openai_key[:4]}...{openai_key[-4:]}" if len(openai_key) > 8 else "OPENAI_KEY"
        try:
            logger.info(f"🔄 [AI FALLBACK] Gemini chain failed. Attempting OpenAI gpt-4o-mini (Key: [{masked_openai}])...")
            openai_result = _try_openai_model(openai_key, text)
            from data_fetch_status import mark_success
            mark_success('gemini')  # AI worker health marked OK
            return openai_result
        except Exception as oai_err:
            oai_err_str = str(oai_err).replace(openai_key, "[REDACTED_KEY]")
            logger.warning(f"❌ [OPENAI FALLBACK FAILED] {oai_err_str}")
            errors.append(f"OpenAI: {oai_err_str}")

    from data_fetch_status import mark_failure
    final_error = errors[-1] if errors else "All AI models failed or all Gemini keys are 7-day blacklisted."
    mark_failure('gemini', final_error)
    return {"error": "All AI models in the fallback chain failed.", "details": errors}
