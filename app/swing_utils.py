import numpy as np
import pandas as pd
from decimal import Decimal
from typing import Optional, List
import ta

from core_enums import PivotKind, RejectionReason
from core_models import (
    SwingPoint, ImpulseLeg, PullbackStructure, TriggerSignal,
    StageResult, PullbackCandidate, DataQualityError
)

def round_to_tick(price: float, tick_size: float = 0.05) -> Decimal:
    """
    Rounds price to nearest NSE tick size (default 0.05).
    """
    if price is None or np.isnan(price):
        return Decimal("0.00")
    rounded = round(round(price / tick_size) * tick_size, 2)
    return Decimal(f"{rounded:.2f}")

def check_data_quality(historical_view: pd.DataFrame):
    """
    Validates data quality invariants before any swing calculations.
    """
    if not historical_view.attrs.get("adjusted", False):
        raise DataQualityError(RejectionReason.REJ_UNADJUSTED_DATA)
        
    required_cols = ['Open', 'High', 'Low', 'Close']
    for col in required_cols:
        if col in historical_view.columns and historical_view[col].isna().any():
            raise DataQualityError(RejectionReason.REJ_PRICE_NAN)

def calculate_atr(historical_view: pd.DataFrame, window: int = 14) -> np.ndarray:
    """
    Standardized ATR re-using ta.volatility.average_true_range (matches technical_indicators.py).
    """
    if 'ATR' in historical_view.columns and not historical_view['ATR'].isna().all():
        return historical_view['ATR'].values
        
    high = historical_view['High']
    low = historical_view['Low']
    close = historical_view['Close']
    
    atr_series = ta.volatility.average_true_range(high, low, close, window=window)
    # Fill leading NaNs with simple High - Low range for stability
    tr_fallback = high - low
    atr_series = atr_series.fillna(tr_fallback)
    return atr_series.values

def detect_confirmed_pivots(historical_view: pd.DataFrame, lookback: int, confirmation_bars: int) -> List[SwingPoint]:
    """
    FROZEN definition:
      bar i is a pivot high if high[i] >= all of high[i-lookback : i+confirmation_bars+1]
      AND at least `confirmation_bars` bars exist between i and as_of_index.

    Returns plateau pivot as the LAST equal bar in the plateau.
    """
    check_data_quality(historical_view)
    
    n = len(historical_view)
    if n < lookback + confirmation_bars + 1:
        return []
        
    highs = historical_view['High'].values
    dates = historical_view['Date'].values if 'Date' in historical_view.columns else historical_view.index
    
    pivots = []
    
    for i in range(lookback, n - confirmation_bars):
        start_idx = max(0, i - lookback)
        end_idx = i + confirmation_bars + 1
        
        window = highs[start_idx:end_idx]
        local_max = np.max(window)
        
        if abs(highs[i] - local_max) < 1e-8:
            future_window = highs[i+1 : end_idx]
            past_window = highs[start_idx : i]
            
            is_plateau = False
            if len(past_window) > 0 and np.any(np.abs(past_window - local_max) < 1e-8):
                is_plateau = True
                
            if len(future_window) > 0 and np.any(np.abs(future_window - local_max) < 1e-8):
                # There is a future equal bar. Let the future bar be the pivot.
                continue
                
            pivots.append(SwingPoint(
                index=i,
                date=pd.to_datetime(dates[i]).date(),
                price=float(highs[i]),
                kind=PivotKind.HIGH,
                is_plateau=is_plateau
            ))
            
    return pivots

def select_pullback_origin(pivots: List[SwingPoint], historical_view: pd.DataFrame, config: dict) -> Optional[ImpulseLeg]:
    """
    Walk pivots newest -> oldest. Accept the first pivot high whose PRECEDING
    upleg (starting after the previous confirmed pivot high or start of window)
    satisfies BOTH gain_pct and atr_multiple.
    """
    if not pivots:
        return None
        
    atr = calculate_atr(historical_view, window=14)
    lows = historical_view['Low'].values
    dates = historical_view['Date'].values if 'Date' in historical_view.columns else historical_view.index
    
    min_gain_pct = config.get("MIN_IMPULSE_GAIN_PCT", 8.0)
    min_atr_mult = config.get("MIN_IMPULSE_ATR", 3.0)
    
    for pivot in reversed(pivots):
        if pivot.index == 0:
            continue
            
        # Find preceding confirmed pivot high (if any)
        prev_pivots = [p for p in pivots if p.index < pivot.index]
        start_search_idx = prev_pivots[-1].index if prev_pivots else 0
        
        # Bound search window to MAX_IMPULSE_BARS (default: 20 bars)
        max_impulse_bars = config.get("MAX_IMPULSE_BARS", 20)
        start_search_idx = max(start_search_idx, pivot.index - max_impulse_bars)
        
        preceding_lows = lows[start_search_idx:pivot.index]
        if len(preceding_lows) == 0:
            continue
            
        min_offset = int(np.argmin(preceding_lows))
        min_idx = start_search_idx + min_offset
        min_price = preceding_lows[min_offset]
        
        if min_price <= 0:
            continue
            
        gain_pct = (pivot.price - min_price) / min_price * 100
        
        pivot_atr = atr[pivot.index]
        if pivot_atr is None or pivot_atr <= 0 or np.isnan(pivot_atr):
            continue
            
        atr_mult = (pivot.price - min_price) / pivot_atr
        
        if gain_pct >= min_gain_pct and atr_mult >= min_atr_mult:
            start_point = SwingPoint(
                index=min_idx,
                date=pd.to_datetime(dates[min_idx]).date(),
                price=float(min_price),
                kind=PivotKind.LOW,
                is_plateau=False
            )
            
            volumes = historical_view['Volume'].values[min_idx:pivot.index+1]
            valid_volumes = volumes[volumes > 0]
            med_vol = float(np.median(valid_volumes)) if len(valid_volumes) > 0 else 0.0
            
            return ImpulseLeg(
                start=start_point,
                end=pivot,
                gain_pct=float(gain_pct),
                atr_multiple=float(atr_mult),
                median_volume=med_vol
            )
            
    return None

def count_internal_pivots(pb_view: pd.DataFrame) -> int:
    """
    Count internal swing lows in the pullback structure.
    A day where low is lower than both adjacent days.
    """
    if len(pb_view) < 3:
        return 0
    lows = pb_view['Low'].values
    count = 0
    for i in range(1, len(lows)-1):
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            count += 1
    return count

def count_confirmed_pullbacks_since_trend_start(historical_view: pd.DataFrame) -> int:
    """
    [DEFERRED POST-v1] Trend maturity tracking.
    Returns 0 in v1 (maturity penalty is calculated downstream in scoring).
    
    [AUDIT-P1] This function always returns 0. The maturity penalty dict in scoring
    is {0: 0, 1: 0, 2: -3, 3: -6}, so the first pullback in any trend always receives
    zero penalty. Implement actual pivot counting here to activate maturity penalties
    for 2nd and 3rd pullbacks in an aging trend.
    """
    return 0

def gate(stage: str, condition: bool, gate_name: str, passed: bool, observed: float = None, 
         threshold: float = None, comparator: str = "", message: str = None) -> StageResult:
    return StageResult(
        stage=stage,
        gate=gate_name,
        passed=passed,
        observed_value=observed,
        threshold=threshold,
        comparator=comparator,
        message=message
    )

def measure_pullback(historical_view: pd.DataFrame, impulse: ImpulseLeg, config: dict, debug: bool = False) -> PullbackStructure:
    trigger_index = len(historical_view) - 1
    # Pullback leg EXCLUDES the trigger bar
    pb = historical_view.iloc[impulse.end.index + 1 : trigger_index]
    dates = historical_view['Date'].values if 'Date' in historical_view.columns else historical_view.index
    
    ps = PullbackStructure(
        symbol=historical_view.attrs.get('symbol', 'UNKNOWN'),
        as_of_date=pd.to_datetime(dates[-1]).date(),
        impulse=impulse,
        pullback_low=None,
        depth_pct=None,
        duration_bars=len(pb),
        volume_ratio=None,
        internal_swing_count=0,
        closed_below_sma50=False,
        min_rsi_during_pullback=None,
        pullback_count_in_trend=0,
        valid=True,
        rejection_reason=None,
        stage_results=[],
        debug=None
    )
    
    if pb.empty:
        ps.valid = False
        ps.rejection_reason = RejectionReason.REJ_DURATION_SHORT
        ps.stage_results.append(gate("PHASE_B", True, RejectionReason.REJ_DURATION_SHORT.name, False, 0, config.get("MIN_DURATION", 3), ">=", "Pullback bar count is 0"))
        return ps
        
    lows = pb['Low'].values
    min_pb_idx = int(np.argmin(lows))
    min_price = lows[min_pb_idx]
    
    global_min_idx = impulse.end.index + 1 + min_pb_idx
    
    ps.pullback_low = SwingPoint(
        index=global_min_idx,
        date=pd.to_datetime(dates[global_min_idx]).date(),
        price=float(min_price),
        kind=PivotKind.LOW,
        is_plateau=False
    )
    
    impulse_range = impulse.end.price - impulse.start.price
    ps.depth_pct = float((impulse.end.price - min_price) / impulse_range * 100) if impulse_range > 0 else 100.0
    
    abs_depth_pct = float((impulse.end.price - min_price) / impulse.end.price * 100)
    
    pb_vols = pb['Volume'].values
    valid_pb_vols = pb_vols[pb_vols > 0]
    pb_med_vol = float(np.median(valid_pb_vols)) if len(valid_pb_vols) > 0 else 0.0
    
    if impulse.median_volume > 0:
        ps.volume_ratio = float(pb_med_vol / impulse.median_volume)
    else:
        ps.volume_ratio = 0.0
        
    ps.internal_swing_count = count_internal_pivots(pb)
    
    if 'SMA50' in pb.columns:
        # [FIX SHAKEOUT_PULLBACK] Allow minor 1-2 bar undercuts (<= 1.5% below SMA50) during pullback,
        # unless SMA50 is sloping downward (trend breakdown) or undercut is >1.5% deep.
        sma50_vals = pb['SMA50'].values
        sma50_sloping_down = len(sma50_vals) >= 2 and (sma50_vals[-1] < sma50_vals[0])
        undercut_mask = pb['Close'].values < (sma50_vals * 0.985)
        closed_too_deep = bool(np.any(pb['Close'].values < (sma50_vals * 0.985)))
        ps.closed_below_sma50 = bool(np.sum(pb['Close'].values < sma50_vals) > 2 or closed_too_deep or (sma50_sloping_down and np.any(pb['Close'].values < sma50_vals)))
    else:
        ps.closed_below_sma50 = False
        
    if 'RSI' in pb.columns:
        rsi_vals = pb['RSI'].dropna().values
        ps.min_rsi_during_pullback = float(np.nanmin(rsi_vals)) if len(rsi_vals) > 0 else None
    
    ps.pullback_count_in_trend = count_confirmed_pullbacks_since_trend_start(historical_view)
    
    gates = []
    
    # REJ_STRUCTURE_RESET
    max_pb_close = np.max(pb['Close'].values)
    is_reset = max_pb_close > impulse.end.price
    gates.append(gate("PHASE_B", is_reset, RejectionReason.REJ_STRUCTURE_RESET.name, not is_reset, max_pb_close, impulse.end.price, "<="))
    
    # Depth
    gates.append(gate("PHASE_B", ps.depth_pct < config.get("MIN_DEPTH_PCT", 23.6), RejectionReason.REJ_DEPTH_TOO_SHALLOW.name, 
                      ps.depth_pct >= config.get("MIN_DEPTH_PCT", 23.6), ps.depth_pct, config.get("MIN_DEPTH_PCT", 23.6), ">="))
    gates.append(gate("PHASE_B", ps.depth_pct > config.get("MAX_DEPTH_PCT", 61.8), RejectionReason.REJ_DEPTH_TOO_DEEP.name,
                      ps.depth_pct <= config.get("MAX_DEPTH_PCT", 61.8), ps.depth_pct, config.get("MAX_DEPTH_PCT", 61.8), "<="))
                      
    # Absolute Floor
    gates.append(gate("PHASE_B", abs_depth_pct < 2.0, RejectionReason.REJ_DEPTH_TOO_SHALLOW.name,
                      abs_depth_pct >= 2.0, abs_depth_pct, 2.0, ">="))
                      
    # Duration
    gates.append(gate("PHASE_B", ps.duration_bars < config.get("MIN_DURATION", 3), RejectionReason.REJ_DURATION_SHORT.name,
                      ps.duration_bars >= config.get("MIN_DURATION", 3), ps.duration_bars, config.get("MIN_DURATION", 3), ">="))
    gates.append(gate("PHASE_B", ps.duration_bars > config.get("MAX_DURATION", 20), RejectionReason.REJ_DURATION_LONG.name,
                      ps.duration_bars <= config.get("MAX_DURATION", 20), ps.duration_bars, config.get("MAX_DURATION", 20), "<="))
                      
    # Orderly
    gates.append(gate("PHASE_B", ps.internal_swing_count > config.get("MAX_INTERNAL_SWINGS", 2), RejectionReason.REJ_DISORDERLY_PULLBACK.name,
                      ps.internal_swing_count <= config.get("MAX_INTERNAL_SWINGS", 2), ps.internal_swing_count, config.get("MAX_INTERNAL_SWINGS", 2), "<="))
                      
    # Volume
    gates.append(gate("PHASE_B", ps.volume_ratio > config.get("MAX_PB_VOLUME_RATIO", 0.75), RejectionReason.REJ_VOLUME_NOT_CONTRACTING.name,
                      ps.volume_ratio <= config.get("MAX_PB_VOLUME_RATIO", 0.75), ps.volume_ratio, config.get("MAX_PB_VOLUME_RATIO", 0.75), "<="))
                      
    # SMA50
    gates.append(gate("PHASE_B", ps.closed_below_sma50, RejectionReason.REJ_CLOSED_BELOW_SMA50.name, not ps.closed_below_sma50, 1 if ps.closed_below_sma50 else 0, 0, "=="))
    
    # RSI
    if ps.min_rsi_during_pullback is not None:
        gates.append(gate("PHASE_B", ps.min_rsi_during_pullback < 35.0, RejectionReason.REJ_RSI_FLOOR.name,
                          ps.min_rsi_during_pullback >= 35.0, ps.min_rsi_during_pullback, 35.0, ">="))
                          
    ps.stage_results = gates
    
    for g in gates:
        if not g.passed:
            ps.valid = False
            ps.rejection_reason = RejectionReason[g.gate]
            break
            
    if debug:
        ps.debug = {
            "debug_version": 1,
            "detected_plateau": impulse.end.is_plateau,
            "impulse_gain_pct": impulse.gain_pct,
            "impulse_atr_mult": impulse.atr_multiple,
            "rejection_gate": ps.rejection_reason.name if ps.rejection_reason else None,
            "anchor_start_index": impulse.start.index,
            "anchor_end_index": impulse.end.index,
            "pullback_bars": ps.duration_bars,
            "pullback_volume_median": pb_med_vol,
            "impulse_volume_median": impulse.median_volume,
            "internal_pivots_found": ps.internal_swing_count
        }
        
    return ps

def detect_resumption_trigger(historical_view: pd.DataFrame, ps: PullbackStructure, config: dict) -> TriggerSignal:
    t = historical_view.iloc[-1]
    dates = historical_view['Date'].values if 'Date' in historical_view.columns else historical_view.index
    
    t_idx = len(historical_view) - 1
    t_date = pd.to_datetime(dates[-1]).date()
    t_open = float(t['Open'])
    t_high = float(t['High'])
    t_low = float(t['Low'])
    t_close = float(t['Close'])
    t_vol = float(t['Volume'])
    
    atr = calculate_atr(historical_view, window=14)
    atr_val = atr[-1]
    
    body = abs(t_close - t_open)
    range_ = t_high - t_low
    upper_wick = t_high - max(t_open, t_close)
    
    opens = historical_view['Open'].values
    closes = historical_view['Close'].values
    highs = historical_view['High'].values
    prev_open = opens[-2] if len(opens) > 1 else t_open
    prev_close = closes[-2] if len(closes) > 1 else t_open
    prev_high = highs[-2] if len(highs) > 1 else t_high
    
    gap_pct = (t_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
    body_atr_ratio = body / atr_val if atr_val > 0 else 0
    upper_wick_ratio = upper_wick / range_ if range_ > 0 else 0
    close_loc = (t_close - t_low) / range_ if range_ > 0 else (1.0 if t_close > prev_close else 0.0)
    
    pb = historical_view.iloc[ps.impulse.end.index + 1 : t_idx]
    pb_vols = pb['Volume'].values
    valid_pb_vols = pb_vols[pb_vols > 0]
    pb_med_vol = float(np.median(valid_pb_vols)) if len(valid_pb_vols) > 0 else 0.0
    
    vol_mult = t_vol / pb_med_vol if pb_med_vol > 0 else 0
    
    is_full_high_takeover = bool(t_close > prev_high)
    is_bullish_engulfing = bool(t_close > prev_open and t_open < prev_close)
    
    trig = TriggerSignal(
        date=t_date,
        entry_price=round_to_tick(t_close),
        trigger_low=round_to_tick(t_low),
        body_atr_ratio=float(body_atr_ratio),
        upper_wick_ratio=float(upper_wick_ratio),
        gap_pct=float(gap_pct),
        volume_mult=float(vol_mult),
        valid=True,
        rejection_reason=None,
        close_position=float(close_loc),
        is_full_high_takeover=is_full_high_takeover,
        is_bullish_engulfing=is_bullish_engulfing
    )
    
    gates = []
    
    is_before_low = t_idx <= ps.pullback_low.index
    gates.append(gate("PHASE_C", is_before_low, RejectionReason.REJ_TRIGGER_BEFORE_LOW.name, not is_before_low))
    
    is_bearish = t_close <= t_open
    gates.append(gate("PHASE_C", is_bearish, RejectionReason.REJ_NOT_BULLISH.name, not is_bearish))
    
    # [FIX PULLBACK_TRIG] Take out yesterday's body/realized price instead of high wick
    no_takeout = t_close <= max(prev_close, prev_open)
    gates.append(gate("PHASE_C", no_takeout, RejectionReason.REJ_NO_PRIOR_HIGH_TAKEOUT.name, not no_takeout))
    
    gates.append(gate("PHASE_C", body_atr_ratio < config.get("MIN_BODY_ATR", 0.5), RejectionReason.REJ_WEAK_BODY.name,
                      body_atr_ratio >= config.get("MIN_BODY_ATR", 0.5), body_atr_ratio, config.get("MIN_BODY_ATR", 0.5), ">="))
                      
    gates.append(gate("PHASE_C", upper_wick_ratio > config.get("MAX_UPPER_WICK", 0.4), RejectionReason.REJ_EXCESSIVE_WICK.name,
                      upper_wick_ratio <= config.get("MAX_UPPER_WICK", 0.4), upper_wick_ratio, config.get("MAX_UPPER_WICK", 0.4), "<="))
                      
    gates.append(gate("PHASE_C", gap_pct > config.get("MAX_ENTRY_GAP_PCT", 3.0), RejectionReason.REJ_GAP_TOO_LARGE.name,
                      gap_pct <= config.get("MAX_ENTRY_GAP_PCT", 3.0), gap_pct, config.get("MAX_ENTRY_GAP_PCT", 3.0), "<="))
                      
    gates.append(gate("PHASE_C", vol_mult < config.get("TRIGGER_VOL_MULT", 1.3), RejectionReason.REJ_TRIGGER_VOLUME.name,
                      vol_mult >= config.get("TRIGGER_VOL_MULT", 1.3), vol_mult, config.get("TRIGGER_VOL_MULT", 1.3), ">="))
                      
    gates.append(gate("PHASE_C", close_loc < config.get("MIN_CLOSE_LOCATION", 0.6), RejectionReason.REJ_WEAK_CLOSE.name,
                      close_loc >= config.get("MIN_CLOSE_LOCATION", 0.6), close_loc, config.get("MIN_CLOSE_LOCATION", 0.6), ">="))
                      
    for g in gates:
        if not g.passed:
            return TriggerSignal(
                date=trig.date,
                entry_price=trig.entry_price,
                trigger_low=trig.trigger_low,
                body_atr_ratio=trig.body_atr_ratio,
                upper_wick_ratio=trig.upper_wick_ratio,
                gap_pct=trig.gap_pct,
                volume_mult=trig.volume_mult,
                valid=False,
                rejection_reason=RejectionReason[g.gate],
                close_position=trig.close_position,
                is_full_high_takeover=trig.is_full_high_takeover,
                is_bullish_engulfing=trig.is_bullish_engulfing
            )
            
    return trig
