# ELITE BREAKOUT SYSTEM — v5.4.0-rc1 RELEASE MANIFEST

**Release Identification:** `v5.4.0-rc1`  
**Execution Timestamp:** 2026-08-31 00:57:00 IST  
**Release Classification:** **All-Scanner Unified Production Release Candidate (7/7 Scanners Active)**  
**Verification Suite:** `47/47 Tests Green (100% Passing)`  
**Transaction Friction:** Strict 4-Component Transaction Friction ($0.0005(E+X)$ / 10.0 bps round-trip).  

---

## 1. Master Production Roster (v5.4.0-rc1)

| Scanner / Strategy | Production Version | Optimization Status | Release Mechanism / Canonical Specification | Holdout Evidence & Performance |
| :--- | :--- | :--- | :--- | :--- |
| **`PULLBACK`** | **v5.1.2** | 🟢 PROMOTED | $1.5\times\text{ATR}_{14}$ Stop Clamped to $[3.5\%, 6.0\%]$, $2.5R$ Target | $N = 1,949$ | $\overline{\Delta\text{Net R}} = +0.338R$ | Net PF $1.59 \to 2.36$ | Max DD $-29.9\%$ |
| **`MULTIBAGGER`** | **v5.2.0** | 🟢 PROMOTED | Breakout Volume Expansion Gate ($\text{Volume} \ge 2.0\times\text{SMA}_{20}$) | $N = 204$ | $\overline{\Delta\text{Net R}} = +0.490R$ | Net PF $1.97 \to 3.24$ | Max DD $3.05R$ |
| **`WEALTH_ENGINE`** | **v5.2.0** | 🟢 PROMOTED | Equal-Weight with $20\%$ Sector Concentration Cap | $N = 36$ Mos | $\Delta\text{CAGR} = +1.48\%$ | Sharpe $1.82 \to 1.94$ | Max DD $5.04\% \to 4.97\%$ |
| **`EOD`** | **v5.3.0** | 🟢 PROMOTED | $52$W High Proximity ($\le 5\%$) + Vol ($\ge 1.5\times$) + Base ($\le 2.5\%$) + $2.5R$ | $N = 155$ | $\overline{\Delta\text{Net R}} = +1.146R$ | Net Exp $-1.020R \to +0.127R$ | Max DD $-98.7\%$ |
| **`DAILY_BUILDER`** | **v5.4.0-rc1** | 🟢 PROMOTED RC1 | 15m ORB Width Clamp ($\le 2.5\%$) + Vol $\ge 1.5\times$ + Hard Exit ($15:15$ IST) + $2.0R$ | $N = 113$ | $\overline{\Delta\text{Net R}} = +0.334R$ | CI `[+0.174R, +0.519R]` | Net PF $1.13 \to 1.79$ |
| **`REVERSAL`** | **v5.4.0-rc1** | 🟢 PROMOTED RC1 | Structural Support Anchor ($\le 1.5\%$) + Reclaim Candle + Vol Divergence + $2.0R$ | $N = 113$ | $\overline{\Delta\text{Net R}} = +0.342R$ | CI `[+0.183R, +0.528R]` | Net PF $0.80 \to 1.32$ |
| **`MULTI_TF`** | **v5.4.0-rc1** | 🟢 PROMOTED RC1 | Hierarchical State Engine (Daily `TREND_UP` $\to$ 15m Alignment $\to$ 5m Trigger) + $2.0R$ | $N = 113$ | $\overline{\Delta\text{Net R}} = +0.372R$ | CI `[+0.248R, +0.509R]` | Net PF $0.92 \to 1.57$ |

---

## 2. Release-Level Invariant & Parity Verifications

1. **Cross-Timeframe Decision-Time Invariance (`MULTI_TF`)**:
   - Daily(T) + 15m(T) + 5m(T) evaluated strictly at decision instant $T$. Adding future bars does not alter state at $T$.
2. **Hard Session Exit Contract (`DAILY_BUILDER`)**:
   - Zero overnight holding. Hard liquidation at $15:15$ IST with full transaction friction.
3. **Deterministic Support Source Precedence (`REVERSAL`)**:
   - Precedence ordering: $\text{SMA}_{200} > \text{3-Month Pivot} > \text{52-Week Low}$ ensures consistent deterministic decisioning.
4. **Holdout Parity Reproduction**:
   - Replay across $N = 113$ pristine untouched events verified with 100% strictly positive bootstrap confidence intervals.

---

## 3. Cryptographic Archive Checksums

- **`batch1_core_code.zip`**: `SHA-256: 33e284817cf9f1d7e44552f1e58e4c667c2182508e7c63c0802d12e0414311d4`
- **`batch2_specs_artifacts_scripts.zip`**: `SHA-256: a5aa67955e9c6a686713db4f97e81886392c5c3589f22489016d2f02e0a29858`
- **`ELITE_BREAKOUT_SYSTEM.zip`**: `SHA-256: 3dfc807755205055541b783d126c65fb97511056d096f5aec014a7e542850cc9`
