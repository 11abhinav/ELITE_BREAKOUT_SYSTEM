# SHORT_COVERING_DAILY — MULTIPLE-TESTING REGISTER & ERROR CONTROL

**Scope:**
- Pre-registered Primary Baselines: 3 (B1-B3)
- Pre-registered Primary Variants: 10 (D1-D10)
- Counterfactual Permutations: 6 (P1-P6)
- Robustness Representations: 3 (R1, R2, R3)
- Total Hypotheses Evaluated: 22
- Multiple Testing Hurdle: Bonferroni alpha = 0.05 / 22 = 0.00227
- Holdout Evaluation: Single Locked Blind Run (No Post-Hoc Tuning)

### Findings:
All D1-D10 variants evaluated on the untouched holdout produced confidence intervals straddling zero or point estimates <= 0.0R.
Zero variants achieved statistical significance under either nominal (alpha=0.05) or Bonferroni-corrected (alpha=0.00227) thresholds.
