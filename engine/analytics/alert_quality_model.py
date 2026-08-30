"""
Alert Quality Scoring (AQS) Model Engine for Wave 4.
Implements:
  - Strongly regularized Ridge regression with unregularized intercept:
      w = (X^T X + lambda * I)^(-1) X^T (y - y_bar)
      b = y_bar
  - Train-fitted calibration to [0, 100] Alert Quality Scores (zero future leakage).
  - Diagnostic analyzer for "Why AQS was wrong" (High AQS Losers vs Low AQS Winners).
"""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd


@dataclass
class ScalerParameters:
    means: Dict[str, float]
    stds: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreCalibrationParameters:
    raw_min: float
    raw_max: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TrainFittedScaler:
    """
    StandardScaler fitted strictly on training partition data to eliminate future data leakage.
    """

    def __init__(self, parameters: Optional[ScalerParameters] = None):
        self.parameters = parameters

    def fit(self, df: pd.DataFrame) -> "TrainFittedScaler":
        means = {}
        stds = {}
        for col in df.columns:
            vals = df[col].astype(float)
            m = float(vals.mean())
            s = float(vals.std())
            means[col] = m
            stds[col] = s if (s > 1e-6 and not np.isnan(s)) else 1.0

        self.parameters = ScalerParameters(means=means, stds=stds)
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.parameters is None:
            raise RuntimeError("Scaler must be fitted or initialized before transforming.")

        df_out = pd.DataFrame(index=df.index)
        for col in df.columns:
            m = self.parameters.means.get(col, 0.0)
            s = self.parameters.stds.get(col, 1.0)
            df_out[col] = (df[col].astype(float) - m) / s
        return df_out


class RidgeAQSModel:
    """
    Strongly regularized Ridge regression model designed for small sample learning (n=35).
    Produces normalized Alert Quality Scores in [0, 100].
    """

    def __init__(
        self,
        model_id: str = "AQS_EOD_v1",
        scanner_scope: str = "EOD",
        l2_lambda: float = 10.0,
        frozen_operating_point: float = 65.0
    ):
        self.model_id = model_id
        self.scanner_scope = scanner_scope
        self.l2_lambda = l2_lambda
        self.frozen_operating_point = frozen_operating_point
        self.weights: Dict[str, float] = {}
        self.intercept: float = 0.0
        self.calibration: Optional[ScoreCalibrationParameters] = None
        self.is_fitted: bool = False

    def fit(self, X_scaled: pd.DataFrame, y_net: pd.Series) -> "RidgeAQSModel":
        """
        Fits Ridge regression weights with unregularized intercept.
        y_net: realized net trade R after friction.
        """
        cols = list(X_scaled.columns)
        X_mat = X_scaled.values.astype(float)
        y_vec = y_net.values.astype(float)

        n_samples, n_features = X_mat.shape

        # Unregularized intercept: y_bar
        y_mean = float(np.mean(y_vec))
        y_centered = y_vec - y_mean

        # Ridge normal equation on centered target
        XTX = np.dot(X_mat.T, X_mat)
        reg_matrix = self.l2_lambda * np.identity(n_features)
        A = XTX + reg_matrix
        b = np.dot(X_mat.T, y_centered)

        w = np.linalg.solve(A, b)

        self.weights = {cols[i]: round(float(w[i]), 4) for i in range(n_features)}
        self.intercept = round(y_mean, 4)

        # Fit score calibration parameters strictly on training predictions
        raw_train_preds = np.dot(X_mat, w) + y_mean
        p_min = float(np.min(raw_train_preds))
        p_max = float(np.max(raw_train_preds))
        if abs(p_max - p_min) < 1e-4:
            p_min = p_min - 0.5
            p_max = p_max + 0.5

        self.calibration = ScoreCalibrationParameters(raw_min=p_min, raw_max=p_max)
        self.is_fitted = True
        return self

    def predict_score(self, X_scaled: pd.DataFrame) -> pd.Series:
        """
        Computes composite Alert Quality Score in [0, 100] using frozen train calibration.
        """
        if not self.is_fitted or self.calibration is None:
            raise RuntimeError("Model and calibration must be fitted before computing AQS scores.")

        cols = list(X_scaled.columns)
        w_vec = np.array([self.weights.get(c, 0.0) for c in cols])
        X_mat = X_scaled.values.astype(float)

        raw_pred = np.dot(X_mat, w_vec) + self.intercept

        # Linear mapping from [raw_min, raw_max] to [30, 85] clipped to [0, 100]
        denom = self.calibration.raw_max - self.calibration.raw_min
        if denom <= 0: denom = 1.0
        normalized_ratio = (raw_pred - self.calibration.raw_min) / denom
        aqs_scores = np.clip(30.0 + (normalized_ratio * 55.0), 0.0, 100.0)

        return pd.Series(np.round(aqs_scores, 2), index=X_scaled.index)

    def diagnose_model_errors(
        self,
        df_eval: pd.DataFrame,
        scores: pd.Series,
        operating_point: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Identifies High AQS Losers (confidently wrong) and Low AQS Winners (unnecessary suppression).
        """
        cutoff = operating_point if operating_point is not None else self.frozen_operating_point
        is_winner = df_eval["label_A_t1_hit"] == True

        high_aqs_losers = df_eval[(scores >= cutoff) & (~is_winner)].copy()
        low_aqs_winners = df_eval[(scores < cutoff) & is_winner].copy()

        return {
            "operating_point": cutoff,
            "high_aqs_losers_count": len(high_aqs_losers),
            "high_aqs_losers": high_aqs_losers[["symbol", "decision_timestamp", "cf_realized_r"]].to_dict(orient="records"),
            "low_aqs_winners_count": len(low_aqs_winners),
            "low_aqs_winners": low_aqs_winners[["symbol", "decision_timestamp", "cf_realized_r"]].to_dict(orient="records")
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "scanner_scope": self.scanner_scope,
            "l2_lambda": self.l2_lambda,
            "frozen_operating_point": self.frozen_operating_point,
            "weights": self.weights,
            "intercept": self.intercept,
            "calibration": self.calibration.to_dict() if self.calibration else None,
            "is_fitted": self.is_fitted
        }
