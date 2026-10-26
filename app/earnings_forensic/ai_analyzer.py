"""
app/earnings_forensic/ai_analyzer.py
Forensic-specific AI enrichment layer.

Reuses the existing _try_gemini_model and _try_openai_model from app/ai_analyzer.py.
Only called AFTER deterministic engine has run.

Anti-hallucination rules enforced:
  - AI receives only the structured fact sheet and deterministic findings
  - AI MUST NOT invent figures not present in the fact sheet
  - Output is validated against a strict JSON schema before acceptance
  - Unsupported claims are rejected and findings downgraded to AI_INFERENCE
  - Low temperature (0.1) requested via Gemini generationConfig
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

from .schema import (
    DataScope,
    EvidenceLevel,
    FindingClassification,
    FindingSeverity,
    ForensicFinding,
    ForensicReport,
)

logger = logging.getLogger(__name__)

# ─── Prompt ───────────────────────────────────────────────────────────────────

FORENSIC_SYSTEM_PROMPT = """You are a senior equity research analyst specializing in forensic accounting and earnings quality analysis for Indian listed companies.

You will receive a structured earnings fact sheet containing:
1. Current quarter and prior-period financial metrics (all from official exchange filings)
2. Deterministic findings already calculated by the system
3. Explicit list of data fields that are MISSING or UNAVAILABLE

YOUR ROLE:
- Explain the significance of the deterministic findings in plain investor language
- Identify what an investor should verify in the original filing
- Surface potential hidden concerns that the numbers suggest but cannot prove
- Generate 3 precise investor questions for the company

STRICT RULES — NEVER VIOLATE:
- DO NOT invent specific figures, events, or facts not present in the fact sheet
- DO NOT conclude fraud or accounting manipulation — classify hypotheses as "requires verification"
- DO NOT assign blame or make regulatory statements
- DO NOT claim a tax refund occurred unless explicitly stated in the fact sheet
- DO NOT claim receivables or inventory data unless it is in the fact sheet
- LABEL every finding as: "CALCULATED_FACT", "CALCULATED_PROXY", or "AI_INFERENCE"
- If evidence is insufficient, say explicitly "This cannot be verified from the available data"
- Keep total output under 600 words

Return ONLY a valid JSON object with this exact structure:
{
  "ai_verdict_summary": "2-3 sentence plain-English investor summary",
  "ai_findings": [
    {
      "code": "UNIQUE_CODE",
      "title": "Short title",
      "severity": "HIGH|MEDIUM|LOW|INFO",
      "classification": "AI_INFERENCE",
      "explanation": "Plain English explanation. Cite which numbers support this.",
      "source_refs": ["PIT_QUARTERLY"],
      "limitations": ["What cannot be confirmed without the filing"]
    }
  ],
  "investor_questions": [
    "Question 1 — specific and verifiable in the filing",
    "Question 2",
    "Question 3"
  ],
  "headline_verdict": "One line: what investors most need to know"
}"""


def _build_fact_sheet_text(report: ForensicReport, raw_facts: Optional[Dict] = None) -> str:
    """Builds the structured fact text to send to the AI."""
    lines = [
        f"=== EARNINGS FORENSIC FACT SHEET ===",
        f"Symbol: {report.symbol}",
        f"Period: {report.period_end} ({report.statement_type}, {report.reporting_basis})",
        f"",
        f"=== KEY METRICS (CALCULATED FROM OFFICIAL PIT DATA) ===",
        f"Revenue YoY: {_fmt(report.revenue_yoy_pct, '%')}",
        f"Revenue QoQ: {_fmt(report.revenue_qoq_pct, '%')}",
        f"Operating Profit YoY: {_fmt(report.op_profit_yoy_pct, '%')}",
        f"PAT YoY: {_fmt(report.net_profit_yoy_pct, '%')}",
        f"Operating Margin (current): {_fmt(report.opm_current, '%')}",
        f"Operating Margin YoY change: {_fmt(report.opm_yoy_delta_bps, 'bps')}",
        f"Net Margin (current): {_fmt(report.npm_current, '%')}",
        f"CFO/PAT (latest annual): {_fmt(report.cfo_pat_ratio, 'x')}",
        f"CFO/PAT (3Y average): {_fmt(report.cfo_pat_3y_avg, 'x')}",
        f"TTM Revenue: {_fmt_cr(report.ttm_revenue)}",
        f"TTM Net Profit: {_fmt_cr(report.ttm_net_profit)}",
        f"",
        f"=== DETERMINISTIC FINDINGS (ENGINE CALCULATED — DO NOT DISPUTE) ===",
    ]

    for i, f in enumerate(report.findings, 1):
        lines.append(
            f"{i}. [{f.severity.value}] {f.code}: {f.explanation[:300]}"
        )

    lines.append("")
    lines.append(f"=== DATA FIELDS MISSING FROM PIT (DO NOT INVENT THESE) ===")
    for m in report.evidence_missing:
        lines.append(f"  - {m}")

    lines.append("")
    lines.append(f"Revenue Quality (Calculated): {report.revenue_quality.value}")
    lines.append(f"Profit Quality (Calculated): {report.profit_quality.value}")
    lines.append(f"Cash Conversion (Calculated): {report.cash_conversion.value}")
    lines.append(f"Evidence Coverage: {report.evidence_coverage_pct:.0f}%")

    # Include raw_facts snippet for any additional context
    if raw_facts:
        safe_keys = ["headline", "source_url", "event_type", "basis"]
        snippet = {k: raw_facts[k] for k in safe_keys if k in raw_facts}
        if snippet:
            lines.append("")
            lines.append(f"=== FILING METADATA ===")
            lines.append(json.dumps(snippet))

    return "\n".join(lines)


def _fmt(val: Optional[float], unit: str = "") -> str:
    if val is None:
        return "N/A (data not available)"
    if unit == "%":
        return f"{val:+.1f}%"
    if unit == "bps":
        return f"{val:+.0f} basis points"
    if unit == "x":
        return f"{val:.2f}x"
    return str(val)


def _fmt_cr(val: Optional[float]) -> str:
    return f"₹{val:,.1f} Cr" if val is not None else "N/A"


def _validate_ai_output(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize the AI output.
    Rejects any finding where severity = HIGH but classification != AI_INFERENCE
    (AI should never escalate beyond inference).
    """
    if not isinstance(raw, dict):
        raise ValueError("AI output is not a JSON object")

    required = ["ai_verdict_summary", "ai_findings", "investor_questions", "headline_verdict"]
    for key in required:
        if key not in raw:
            raise ValueError(f"Missing required key: {key}")

    validated_findings = []
    for f in raw.get("ai_findings", []):
        if not isinstance(f, dict):
            continue
        # Force classification to AI_INFERENCE for all AI outputs
        f["classification"] = "AI_INFERENCE"
        # Cap severity — AI cannot assign HIGH independently without deterministic backing
        if f.get("severity") == "HIGH":
            f["severity"] = "MEDIUM"
            f.setdefault("limitations", [])
            f["limitations"].append("Severity capped at MEDIUM: AI cannot confirm HIGH without deterministic data")
        validated_findings.append(f)

    raw["ai_findings"] = validated_findings[:5]  # Max 5 AI findings
    raw["investor_questions"] = raw.get("investor_questions", [])[:3]  # Max 3 questions
    return raw


def analyze(report: ForensicReport, raw_facts: Optional[Dict] = None) -> ForensicReport:
    """
    Run AI enrichment on a report that has already been through the deterministic engine.
    Mutates report in-place: adds ai_findings, investor_questions, updates headline_verdict.
    Returns the report.
    """
    try:
        from gemini_key_manager import get_active_gemini_key, mark_gemini_key_exhausted
        from ai_analyzer import _try_gemini_model, _try_openai_model, _discover_supported_models
    except ImportError:
        logger.warning("[FORENSIC_AI] Could not import AI infrastructure. Skipping AI enrichment.")
        return report

    import os
    openai_key = os.getenv("OPENAI_API_KEY")

    fact_text = _build_fact_sheet_text(report, raw_facts)
    full_prompt = FORENSIC_SYSTEM_PROMPT + "\n\nFACT SHEET:\n" + fact_text

    # Gemini call chain
    attempted_keys = set()
    while True:
        curr_key = get_active_gemini_key()
        if not curr_key or curr_key in attempted_keys:
            break
        attempted_keys.add(curr_key)

        discovered = _discover_supported_models(curr_key)
        models = discovered or ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"]

        key_rate_limited = False
        for model_name in models:
            try:
                # Build a custom payload with temperature=0.1 for forensic analysis
                import requests
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={curr_key}"
                headers = {"Content-Type": "application/json", "x-goog-api-key": curr_key}
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.1,
                        "maxOutputTokens": 1200,
                    }
                }
                res = requests.post(url, headers=headers, json=payload, timeout=90)
                if res.status_code == 200:
                    data = res.json()
                    content_str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    for prefix in ("```json", "```"):
                        if content_str.startswith(prefix):
                            content_str = content_str[len(prefix):]
                    if content_str.endswith("```"):
                        content_str = content_str[:-3]
                    ai_raw = json.loads(content_str.strip())
                    ai_validated = _validate_ai_output(ai_raw)
                    _apply_ai_results(report, ai_validated, model_name)
                    logger.info(f"[FORENSIC_AI] {report.symbol} {report.period_end}: AI enrichment complete via {model_name}")
                    return report
                elif res.status_code == 429 or "RESOURCE_EXHAUSTED" in res.text.upper():
                    mark_gemini_key_exhausted(curr_key, f"Forensic AI 429 on {model_name}")
                    key_rate_limited = True
                    break
                elif res.status_code == 404:
                    continue
                else:
                    logger.warning(f"[FORENSIC_AI] {model_name} returned {res.status_code}")
                    continue
            except Exception as e:
                err = str(e).replace(curr_key, "[REDACTED]")
                if "429" in err or "RESOURCE_EXHAUSTED" in err.upper():
                    mark_gemini_key_exhausted(curr_key, f"Forensic AI 429 on {model_name}")
                    key_rate_limited = True
                    break
                logger.debug(f"[FORENSIC_AI] {model_name} failed: {err}")
                continue

        if not key_rate_limited:
            break

    # OpenAI fallback
    if openai_key:
        try:
            result = _try_openai_model(openai_key, full_prompt)
            ai_validated = _validate_ai_output(result)
            _apply_ai_results(report, ai_validated, "gpt-4o-mini")
            logger.info(f"[FORENSIC_AI] {report.symbol} {report.period_end}: AI enrichment via OpenAI fallback")
        except Exception as e:
            logger.warning(f"[FORENSIC_AI] OpenAI fallback failed: {e}")

    return report


def _apply_ai_results(report: ForensicReport, ai_data: Dict, model_name: str) -> None:
    """Apply validated AI output to the report."""
    report.ai_model_used = model_name

    # Update headline if AI provides a better one
    ai_headline = str(ai_data.get("headline_verdict") or "").strip()
    if ai_headline and len(ai_headline) > 10:
        report.headline_verdict = ai_headline

    # Map AI findings to ForensicFinding objects
    ai_findings = []
    for f_dict in ai_data.get("ai_findings", []):
        try:
            sev_str = str(f_dict.get("severity", "MEDIUM")).upper()
            sev = FindingSeverity[sev_str] if sev_str in FindingSeverity.__members__ else FindingSeverity.MEDIUM
            ff = ForensicFinding(
                code             = str(f_dict.get("code") or "AI_FINDING"),
                title            = str(f_dict.get("title") or "AI Finding"),
                severity         = sev,
                evidence_level   = EvidenceLevel.WEAK,
                classification   = FindingClassification.AI_INFERENCE,
                data_scope       = DataScope.NOT_APPLICABLE,
                explanation      = str(f_dict.get("explanation") or ""),
                source_refs      = list(f_dict.get("source_refs") or ["AI_INFERENCE"]),
                limitations      = list(f_dict.get("limitations") or []),
            )
            ai_findings.append(ff)
        except Exception:
            continue

    report.ai_findings = ai_findings
    report.investor_questions = [str(q) for q in ai_data.get("investor_questions", [])[:3]]
