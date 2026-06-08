
import json, os
import numpy as np
import xgboost as xgb

FEATURE_ORDER = [
    "avg_employment","bachelor_or_higher_pct","covid","establishment_count",
    "land_area_sqmi","latitude","longitude","median_age","median_household_income",
    "n_events","n_festivals","n_setlistfm","n_ticketmaster","population","quarter",
    "total_est_attendance","total_expected_attendance","total_festival_attendance",
    "total_wages_usd",
]

def model_fn(model_dir):
    booster = xgb.Booster()
    booster.load_model(os.path.join(model_dir, "xgboost-model.json"))
    return booster

def input_fn(body, content_type):
    if content_type == "application/json":
        obj = json.loads(body)
        rows = obj["instances"] if isinstance(obj, dict) else obj
        if isinstance(rows[0], dict):                 # list of feature dicts
            rows = [[r[k] for k in FEATURE_ORDER] for r in rows]
        return np.asarray(rows, dtype=float)
    if content_type == "text/csv":                    # rows of comma-separated values
        return np.asarray(
            [list(map(float, line.split(","))) for line in body.strip().splitlines()],
            dtype=float,
        )
    raise ValueError("unsupported content type: " + str(content_type))

def predict_fn(arr, booster):
    log_pred = booster.predict(xgb.DMatrix(arr, feature_names=FEATURE_ORDER))
    return np.expm1(log_pred)                          # undo log1p -> dollars

def output_fn(pred, accept):
    return json.dumps({"predictions": pred.tolist()}), "application/json"
