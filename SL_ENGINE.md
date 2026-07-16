# V6.4 Stop Loss & Target Engine Architecture

*This is the canonical single source of truth for the Elite Breakout System's risk and target management engine. The architecture is formally frozen as of V6.4.*

---

## 1. Philosophy
The Stop Loss & Target Engine is designed around a strict Separation of Concerns. It acts purely as a structural risk-definition engine, explicitly decoupling Trade Quality, Position Sizing, and Execution.

The engine operates on the principle of **Predictive Structural Floors and Ceilings**: finding the strongest confluence of historical support below an entry price to anchor the stop, and locating the nearest institutional resistance to anchor the targets.

Execution is strictly deterministic. There are no recovery checks, no volume-trap logic, and no close-basis waiting. If the absolute stop loss is breached by a single tick, the trade is terminated immediately.

---

## 2. Architecture & Component Ownership
To prevent logic sprawl, the system is strictly segmented into single-responsibility engines:

### `MarketStructure` Layer
- **`SupportEngine`**: Heavyweight component. Owns structural cluster creation, overlap scoring, and weighted anchor calculation for Stops. 
- **`ResistanceSelector`**: Lightweight component. Owns filtering and ranking structural ceilings by importance to define Targets.

### `SL Generator` (Orchestrator - V6.4)
**Owns:** Volatility-aware adaptive buffers, RR calculation, Target generation (T1, T2, T3), and Trade Validation (Accept/Reject).
**Never Owns:** Structural discovery. It purely consumes `SupportEngine` and `ResistanceSelector` outputs.

---

## 3. Public Interfaces
The `sl_target_helper.py` exposes dedicated generation methods (`_compute_eod`, `_compute_reversal`, `_compute_multi_tf`) that return a flat dictionary consumed by the Scanners:

```json
{
  "engine_version": "SL_ENGINE_V6",
  "stop_loss": 94.50,
  "structural_failure_stop": 93.80,
  "target_1": 105.20,
  "target_2": 110.50,
  "target_3": null,
  "natural_rr": 2.14,
  "reward_potential_pct": 5.2,
  "target_quality": 84
}
```

---

## 4. Stop Loss Algorithms

### Clustering & Scoring
1. All valid technical supports within `1.5 ATR` of the highest local support form a cluster.
2. The cluster receives a **Final Support Score** equal to the sum of its members' Base Scores plus the Overlap Bonus. 
3. **No momentum or volume data is factored into the Support Score.**

### Weighted Anchor Calculation
The Anchor Price is calculated as the **Weighted Average** of the cluster's members, weighted by their individual Final Support Scores. This ensures the anchor is naturally magnetized toward the strongest structural confluence within the cluster.

### Adaptive Buffer Generation
The final Stop Loss is calculated as: `Anchor Price - (Base Multiplier * Quality Modifier * ATR)`

**Volatility (Base Multiplier):**
- Low Volatility (`ATR < 2%`): `0.5x ATR`
- Normal Volatility: `0.75x ATR`
- High Volatility (`ATR > 6%`): `1.0x ATR`

**Quality Modifier:**
- Strong Support (`Score > 60`): `0.8` (Tighter stop)
- Weak Support (`Score < 30`): `1.2` (Wider stop)
- Normal Support: `1.0`

---

## 5. Target Generation Algorithms (V6.4)

### The Multi-Target Cascade (T1, T2, T3)
Instead of a single target, the engine calculates a sequential exit cascade:
- **T1 (Primary):** Anchored strictly to the nearest institutional Resistance identified by the `ResistanceSelector`.
- **T2 (Secondary):** Projected mathematically (e.g. `Entry + 1.5x ATR`).
- **T3 (Runner):** Projected mathematically (e.g. `Entry + 3.0x ATR`).

### Target Capping by Macro Regime
To prevent intraday and EOD targets from projecting into impossible price levels during weak markets, T2 and T3 are explicitly capped via the `_cap_target` bounds. 
- In **BEAR** regimes, targets are severely compressed (e.g., max 3x ATR).
- In **BULL** regimes, targets are allowed to breathe (e.g., max 8x ATR).

### Target Suppression Logic (Dynamic Profit Taking)
The engine actively suppresses targets based on real-time momentum:
- **Missing T2:** If the stock is highly Overbought (`RSI > 72`), momentum is exhausted. `target_2` is set to `null` to force a 100% exit at T1.
- **Missing T3:** The runner target is ONLY granted to elite setups. It requires `MACD > 0`, RSI not overbought, and `ADX > 25`. If any condition fails, `target_3` is set to `null`.
- **Reversal Scanners:** Mean-reversion trades are highly speculative. The engine hardcodes `target_3` to `null` permanently for all Reversal setups.

---

## 6. Pre-Execution Validation Gates (V6.4)
The engine executes hard rejections before a trade is ever saved to the database:
- **Natural RR Gate:** The distance to the T1 structural resistance must provide at least `1.5` Reward/Risk (or `2.0` for reversals). If resistance is too close to entry, the trade is rejected.
- **Reward Potential Gate:** T1 must be at least `3.5%` away from the entry price (varies by timeframe).

---

## 7. Freeze Governance
The V6.4 Architecture is **strictly feature-frozen**. 
- No new indicators may be added.
- No new support types may be added.
- No new scoring bonuses may be added.
- No execution flows may be changed.

**Any fundamental architectural change requires a new engine version (e.g., V7.0) rather than modifying V6.4 in place.**

---

## 8. Version History
- **V6.4**: Target Generation rewrite. Introduced T1, T2, T3 multi-target cascade. Integrated Macro Regime Target Capping. Added Target Suppression logic based on RSI/MACD exhaustion.
- **V6.3.0**: Final market-structure abstraction. Introduced `ResistanceSelector` to mirror `SupportEngine`. Added standardized JSON rejection metadata.
- **V6.2.2**: `SupportEngine` extracted as stateless module. Official architectural freeze (historic).
- **V6.2.1**: Separation of Concerns. Stripped volume/momentum modifiers from support scores. Implemented weighted anchor. 
- **V6.1**: Shift to strictly deterministic Structural Supports. Scrapped all reactive volume-recovery logic.
