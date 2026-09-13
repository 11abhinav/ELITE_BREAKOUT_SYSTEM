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
                # Rank priority: Next-gen 3.x/frontier models first, followed by 2.5 and 2.0 SOTA reasoning
                def _priority(m_name: str) -> int:
                    m = m_name.lower()
                    if "3.8" in m or "3.7" in m or "3.5" in m or "3.0" in m or "gemini-3" in m: return 0
                    if "2.5-pro" in m: return 1
                    if "2.5-flash" in m: return 2
                    if "2.0-pro" in m: return 3
                    if "2.0-flash" in m and "lite" not in m: return 4
                    if "2.0-flash-lite" in m: return 5
                    if "1.5-pro" in m: return 6
                    if "1.5-flash" in m and "8b" not in m: return 7
                    if "flash" in m: return 8
                    if "pro" in m: return 9
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

    # Fallback Chain 1: Exhaustively attempt with ALL available Gemini Keys & Models
    while True:
        curr_key = get_active_gemini_key()
        if not curr_key:
            logger.warning("🚨 [GEMINI ALL EXHAUSTED] All configured Gemini keys have hit verified rate limits.")
            break

        masked_key = f"{curr_key[:4]}...{curr_key[-4:]}" if len(curr_key) > 8 else "GEMINI_KEY"
        discovered = _discover_supported_models(curr_key)
        gemini_models = discovered if discovered else [
            "gemini-3.0-pro",
            "gemini-3.0-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-pro-exp-02-05",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.5-flash-lite"
        ]

        key_hit_rate_limit = False

        for model in gemini_models:
            try:
                logger.info(f"Attempting AI analysis with {model} (Key: [{masked_key}])...")
                result = _try_gemini_model(model, curr_key, text)
                result["key_used"] = masked_key
                from data_fetch_status import mark_success
                mark_success('gemini')
                return result
            except Exception as e:
                err_str = str(e).replace(curr_key, "[REDACTED_KEY]")
                # Strict check for genuine Rate Limit / Resource Exhausted
                is_rate_limited = any(term in err_str.upper() for term in [
                    "429", "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED", "QUOTA EXCEEDED"
                ])
                if is_rate_limited:
                    logger.warning(f"❌ [CONFIRMED RATE LIMIT] Model {model} hit verified quota limit on key [{masked_key}]. Blacklisting key for 24h and auto-switching...")
                    errors.append(f"{model} Rate Limited (Key: [{masked_key}])")
                    mark_gemini_key_exhausted(curr_key, f"Verified 429 Quota Exceeded on {model}")
                    key_hit_rate_limit = True
                    break  # Break model loop to advance to next key in while loop
                elif "404" in err_str or "NOT_FOUND" in err_str:
                    logger.warning(f"Skipping {model} on key [{masked_key}] due to 404/NOT_FOUND: {err_str}")
                    continue
                else:
                    logger.warning(f"{model} non-quota failure on key [{masked_key}]: {err_str}")
                    errors.append(f"{model}: {err_str}")
                    continue

        if not key_hit_rate_limit:
            # If all models failed for non-quota reasons (e.g. content policy or 404s), exit loop to avoid infinite loop
            break

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
    # [RULE 67 - FIX RATIONALE]: Updated message to reflect 1-day (24-hour) Gemini blacklist policy.
    final_error = errors[-1] if errors else "All AI models failed or all Gemini keys are 1-day blacklisted."
    mark_failure('gemini', final_error)
    return {"error": "All AI models in the fallback chain failed.", "details": errors}


# =====================================================================================
# UNIVERSAL MULTI-STREAM CORPORATE DOSSIER REASONING ENGINE
# =====================================================================================

DOSSIER_SYSTEM_PROMPT = """You are a senior institutional equity research analyst and corporate governance forensic auditor.
You will be provided with:
1. Chronological timeline of structured corporate events (Order wins, capex, M&A, regulatory filings, defaults, rating actions).
2. Institutional analyst research reports and consensus target revisions.
3. Earnings call transcripts (if available).

CRITICAL EPISTEMIC SEPARATION:
- Level 1 FACT: Exchange filings and regulatory orders are established facts.
- Level 2 VERIFIED ASSESSMENT: Credit rating actions and statutory audit communications are verified assessments.
- Level 3 ANALYST OPINION: Broker target prices and recommendations are market opinions.
- Level 4 MODEL INFERENCE: Your synthesis.

CRITICAL INVARIANT:
- An analyst target price, upgrade, or optimistic guidance can NEVER override or neutralize an active debt default, NCLT insolvency, or statutory auditor resignation.

EVALUATION RUBRIC:
1. Contradiction Detection: Explicitly detect any discrepancy between management optimistic claims and regulatory/credit filings.
2. Management Credibility: Score 1-10 based on whether past promises/guidance matched actual execution.
3. Corporate Catalyst Score (0 to 100): Quantify growth momentum (material order wins, capacity expansion, M&A).
4. Corporate Risk Score (0 to 100): Quantify structural, governance, debt, and litigation hazards.
5. Governance Score: 1-10 scale.

Return a strict JSON object with EXACTLY this schema:
{
    "catalyst_score": (integer 0-100),
    "risk_score": (integer 0-100),
    "governance_score": (float 1.0-10.0),
    "management_credibility_score": (float 1.0-10.0),
    "contradiction_detected": (boolean),
    "contradiction_details": (array of strings, empty if none),
    "net_verdict": ("STRONG_TAILWIND" | "MODERATE_POSITIVE" | "NEUTRAL" | "ELEVATED_RISK" | "SEVERE_RED_FLAG_AVOID"),
    "executive_summary": (string thorough research synthesis),
    "positive_catalysts": (array of strings),
    "severe_hazards_and_red_flags": (array of strings),
    "evidence_chain": (array of objects with "fact", "source", "evidence_class", "materiality")
}"""


def _try_gemini_dossier(model_name: str, gemini_key: str, prompt_text: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": gemini_key
    }
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": DOSSIER_SYSTEM_PROMPT + "\n\nCORPORATE DOSSIER INPUTS:\n" + prompt_text}]}
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
            raise Exception(f"Failed to parse dossier response: {e}")
    else:
        raise Exception(f"API Error ({res.status_code}): {res.text}")


def analyze_full_corporate_dossier(
    symbol: str,
    timeline_events: list,
    analyst_consensus: dict,
    concall_text: str = ""
) -> dict:
    """
    Synthesizes the complete longitudinal corporate dossier for a stock.
    Enforces deterministic safety gating, calculates dual-stream catalyst vs risk scores,
    generates immutable point-in-time snapshots, and updates materialized current state.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from corporate_event_classifier import evaluate_deterministic_hard_risk_gate
    from database import save_company_intelligence_snapshot, save_company_intelligence_current

    now_iso = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()

    # 1. DETERMINISTIC HARD-RISK SAFETY GATE (Precedes LLM)
    hard_gate_status, active_hazards = evaluate_deterministic_hard_risk_gate(timeline_events)

    # 2. Build synthesis prompt payload
    events_str = ""
    for ev in timeline_events[:15]:
        dt = str(ev.get("event_date", ""))[:10]
        cat = ev.get("category", "")
        hl = ev.get("headline", "")
        tier = ev.get("source_tier", "")
        ev_class = ev.get("evidence_class", "FACT")
        mat = ev.get("materiality_score", 5.0)
        events_str += f"- [{dt}] ({cat} | {tier} | {ev_class} | Materiality: {mat}): {hl}\n"

    analyst_str = f"""Total Covering Analysts: {analyst_consensus.get('total_covering_analysts', 0)}
Buy: {analyst_consensus.get('buy_count', 0)} | Hold: {analyst_consensus.get('hold_count', 0)} | Sell: {analyst_consensus.get('sell_count', 0)}
Median Target: ₹{analyst_consensus.get('median_target_price')} (Upside: {analyst_consensus.get('upside_pct')}%)
Target Dispersion: {analyst_consensus.get('target_dispersion_pct')}%
30-Day Target Revision: {analyst_consensus.get('target_revision_30d_pct')}% | 90-Day: {analyst_consensus.get('target_revision_90d_pct')}%
Analyst Revision Score: {analyst_consensus.get('analyst_revision_score', 50)}/100
"""

    prompt_payload = f"""SYMBOL: {symbol}

--- CHRONOLOGICAL CORPORATE EVENTS (PAST 12 MONTHS) ---
{events_str if events_str else 'No major non-routine announcements filed.'}

--- INSTITUTIONAL ANALYST RESEARCH CONSENSUS ---
{analyst_str}

--- EARNINGS CALL GUIDANCE & COMMENTARY ---
{concall_text[:30000] if concall_text else 'No earnings call transcript available.'}
"""

    # 3. LLM Reasoning via Gemini Multi-Key & Model Cascade
    ai_result = None
    from gemini_key_manager import get_active_gemini_key, mark_gemini_key_exhausted

    while True:
        curr_key = get_active_gemini_key()
        if not curr_key:
            break

        masked_key = f"{curr_key[:4]}...{curr_key[-4:]}" if len(curr_key) > 8 else "GEMINI_KEY"
        discovered = _discover_supported_models(curr_key)
        gemini_models = discovered if discovered else [
            "gemini-3.0-pro", "gemini-3.0-flash", "gemini-2.5-pro", "gemini-2.5-flash",
            "gemini-2.0-pro-exp-02-05", "gemini-2.0-flash", "gemini-2.0-flash-lite"
        ]

        key_hit_limit = False
        for model in gemini_models:
            try:
                ai_result = _try_gemini_dossier(model, curr_key, prompt_payload)
                logger.info(f"✅ [INTELLIGENCE DOSSIER] Successfully synthesized {symbol} with Gemini model '{model}'")
                break
            except Exception as e:
                err_str = str(e)
                logger.warning(f"⚠️ [INTELLIGENCE DOSSIER MODEL ATTEMPT FAILED] Symbol={symbol} | Model={model} | Error={err_str}")
                if any(t in err_str.upper() for t in ("429", "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED")):
                    mark_gemini_key_exhausted(curr_key, f"Verified 429 on {model}")
                    key_hit_limit = True
                    break
                continue

        if ai_result or not key_hit_limit:
            break

    # Fallback default values if LLM unavailable
    if not ai_result:
        logger.warning(f"⚠️ [INTELLIGENCE DOSSIER LLM OFFLINE] AI models offline/exhausted for {symbol}. Proceeding with deterministic rule-based synthesis.")
        catalyst_score = 60 if any(e.get("category") == "ORDER_WIN" for e in timeline_events) else 50
        risk_score = 85 if hard_gate_status == "QUARANTINE" else 15
        governance_score = 4.0 if hard_gate_status == "QUARANTINE" else 7.5
        mgmt_cred = 7.0
        contra = False
        net_verdict = "SEVERE_RED_FLAG_AVOID" if hard_gate_status == "QUARANTINE" else "NEUTRAL"
        exec_summary = f"Longitudinal dossier compiled with {len(timeline_events)} event(s)."
        pos_cats = [e.get("headline", "") for e in timeline_events if e.get("category") in ("ORDER_WIN", "CAPEX_EXPANSION")][:3]
        hazards = [e.get("headline", "") for e in active_hazards]
        ev_chain = [{"fact": e.get("headline"), "source": e.get("source_name", "NSE"), "evidence_class": e.get("evidence_class", "FACT"), "materiality": e.get("materiality_score", 5.0)} for e in timeline_events[:5]]
    else:
        catalyst_score = int(ai_result.get("catalyst_score", 50))
        risk_score = int(ai_result.get("risk_score", 10))
        governance_score = float(ai_result.get("governance_score", 7.0))
        mgmt_cred = float(ai_result.get("management_credibility_score", 7.0))
        contra = bool(ai_result.get("contradiction_detected", False))
        net_verdict = ai_result.get("net_verdict", "NEUTRAL")
        exec_summary = ai_result.get("executive_summary", "")
        pos_cats = ai_result.get("positive_catalysts", [])
        hazards = ai_result.get("severe_hazards_and_red_flags", [])
        ev_chain = ai_result.get("evidence_chain", [])

    # Override verdict if hard-risk gate quarantined
    if hard_gate_status == "QUARANTINE":
        net_verdict = "SEVERE_RED_FLAG_AVOID"
        risk_score = max(risk_score, 85)

    # Net corporate score: Catalyst - (1.5 * Risk) + Analyst_Boost
    analyst_rev_score = analyst_consensus.get("analyst_revision_score", 50)
    analyst_adj = (analyst_rev_score - 50) * 0.4
    net_score = int(max(-100, min(100, round(catalyst_score - (1.5 * risk_score) + analyst_adj))))

    # 4. Save Immutable Point-in-Time Snapshot
    snap_id = save_company_intelligence_snapshot(
        symbol=symbol,
        as_of_time=now_iso,
        catalyst_score=catalyst_score,
        risk_score=risk_score,
        governance_score=governance_score,
        management_credibility_score=mgmt_cred,
        analyst_revision_score=analyst_rev_score,
        analyst_dispersion_pct=analyst_consensus.get("target_dispersion_pct", 0.0),
        net_score=net_score,
        net_verdict=net_verdict,
        hard_gate_status=hard_gate_status,
        active_open_hazards_count=len(active_hazards),
        contradiction_detected=contra,
        executive_summary=exec_summary,
        evidence_chain=ev_chain,
        consensus_summary=analyst_consensus
    )

    # 5. Materialize into Current View
    summary_payload = {
        "symbol": symbol,
        "as_of_time": now_iso,
        "net_verdict": net_verdict,
        "hard_gate_status": hard_gate_status,
        "catalyst_score": catalyst_score,
        "risk_score": risk_score,
        "governance_score": governance_score,
        "management_credibility_score": mgmt_cred,
        "analyst_revision_score": analyst_rev_score,
        "net_score": net_score,
        "contradiction_detected": contra,
        "active_open_hazards_count": len(active_hazards),
        "positive_catalysts": pos_cats,
        "severe_hazards": hazards,
        "executive_summary": exec_summary,
        "analyst_consensus": analyst_consensus,
        "evidence_chain": ev_chain
    }

    save_company_intelligence_current(
        symbol=symbol,
        latest_snapshot_id=snap_id,
        net_verdict=net_verdict,
        hard_gate_status=hard_gate_status,
        catalyst_score=catalyst_score,
        risk_score=risk_score,
        governance_score=governance_score,
        management_credibility_score=mgmt_cred,
        analyst_revision_score=analyst_rev_score,
        summary_payload=summary_payload
    )

    return summary_payload

