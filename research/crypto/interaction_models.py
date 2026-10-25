"""Frozen nonlinear/linear controls with separate chronological probability calibration."""

import math
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from freqtrade.research.data import read, sha
from freqtrade.research.workflow import source_identity
from research.crypto.conditional_learning import fit_model, predict


def verify_sources(plan):
    for path, digest in plan["source_hashes"].items():
        if sha(Path(path)) != digest:
            raise ValueError("Frozen research source changed")
    for name, expected in plan.get("expected_shared_versions", {}).items():
        if version(name) != expected:
            raise ValueError("Shared numerical runtime changed")
    reference = plan.get("native_source_reference")
    if reference is not None:
        path = Path(reference["path"])
        if sha(path) != reference["sha256"] or source_identity() != read(path)["source_identity"]:
            raise ValueError("Frozen native execution framework changed")


def enable_runtime(plan):
    manifest_path = Path(plan["runtime_manifest"]["path"])
    if sha(manifest_path) != plan["runtime_manifest"]["sha256"]:
        raise ValueError("Isolated model runtime manifest changed")
    manifest = read(manifest_path)
    target = Path(manifest["target"])
    for relative, digest in manifest["files"].items():
        if sha(target / relative) != digest:
            raise ValueError("Isolated model dependency changed")
    sys.path.insert(0, str(target))
    for name, expected in manifest["packages"].items():
        if version(name) != expected:
            raise ValueError("Incorrect model dependency version")
    return {
        name: version(name)
        for name in ["numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl"]
    }


def fit_calibration(probability, outcomes, penalty):
    scores = logit(np.clip(probability, 1e-6, 1 - 1e-6))
    rate = float(np.mean(outcomes))
    if not 0 < rate < 1:
        raise ValueError("Both calibration outcome classes required")

    def objective(weights):
        raw = weights[0] + weights[1] * scores
        residual = expit(raw) - outcomes
        loss = np.mean(np.logaddexp(0, raw) - outcomes * raw) + penalty * weights[1] ** 2 / 2
        gradient = np.array([residual.mean(), np.mean(residual * scores) + penalty * weights[1]])
        return loss, gradient

    result = minimize(
        objective,
        np.array([float(logit(rate)), 1.0]),
        jac=True,
        method="L-BFGS-B",
        bounds=[(None, None), (0, None)],
        options={"maxiter": 500, "gtol": 1e-8, "ftol": 1e-12},
    )
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("Probability calibration did not converge")
    return {
        "intercept": float(result.x[0]),
        "slope": float(result.x[1]),
        "observations": len(scores),
        "objective": float(result.fun),
        "maximum_gradient": float(np.abs(result.jac).max()),
        "iterations": int(result.nit),
    }


def calibrate(calibration, probability):
    return expit(
        calibration["intercept"]
        + calibration["slope"] * logit(np.clip(probability, 1e-6, 1 - 1e-6))
    )


def fit_bundle(kind, x, y, calibration_x, calibration_y, plan):
    binary, calibration_binary = (y > 0).astype(int), (calibration_y > 0).astype(int)
    if kind == "linear":
        model = fit_model(x, y, binary, penalty=plan["linear_penalty"], maximum_iterations=500)
        raw, _ = predict(model, calibration_x)
        bundle = {"kind": kind, "linear": model}
    else:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        classifier = HistGradientBoostingClassifier(**plan["boosting_parameters"])
        regressor = HistGradientBoostingRegressor(**plan["boosting_parameters"])
        classifier.fit(x, binary)
        regressor.fit(x, y)
        raw = classifier.predict_proba(calibration_x)[:, 1]
        bundle = {"kind": kind, "classifier": classifier, "regressor": regressor}
    bundle["calibration"] = fit_calibration(raw, calibration_binary, plan["calibration_penalty"])
    return bundle


def predict_bundle(bundle, x):
    if bundle["kind"] == "linear":
        raw, net = predict(bundle["linear"], x)
    else:
        raw, net = bundle["classifier"].predict_proba(x)[:, 1], bundle["regressor"].predict(x)
    probability = calibrate(bundle["calibration"], raw)
    if not np.isfinite(probability).all() or not np.isfinite(net).all():
        raise ValueError("Nonfinite forecast")
    return raw, probability, net


def scalar_predict(bundle, row):
    """Independent per-tree traversal of the pinned, numeric-only histogram estimators."""

    def trees(model):
        total = float(np.asarray(model._baseline_prediction).ravel()[0])
        for iteration in model._predictors:
            if len(iteration) != 1:
                raise ValueError("Only numeric binary/regression models registered")
            nodes, index = iteration[0].nodes, 0
            while not nodes[index]["is_leaf"]:
                node = nodes[index]
                if node["is_categorical"]:
                    raise ValueError("Unexpected categorical split")
                value = row[int(node["feature_idx"])]
                left = (
                    bool(node["missing_go_to_left"])
                    if math.isnan(value)
                    else value <= node["num_threshold"]
                )
                index = int(node["left"] if left else node["right"])
            total += float(nodes[index]["value"])
        return total

    if bundle["kind"] == "linear":
        model = bundle["linear"]
        vector = [1.0] + [
            (float(value) - mean) / deviation
            for value, mean, deviation in zip(row, model["mean"], model["deviation"], strict=True)
        ]
        raw = float(expit(math.fsum(a * b for a, b in zip(vector, model["logistic"], strict=True))))
        net = math.fsum(a * b for a, b in zip(vector, model["linear"], strict=True))
    else:
        raw, net = float(expit(trees(bundle["classifier"]))), trees(bundle["regressor"])
    c = bundle["calibration"]
    probability = float(
        expit(c["intercept"] + c["slope"] * float(logit(np.clip(raw, 1e-6, 1 - 1e-6))))
    )
    return raw, probability, net
