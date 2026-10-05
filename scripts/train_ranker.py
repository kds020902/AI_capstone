
from pathlib import Path
import argparse
import pandas as pd
import joblib

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error

    df = pd.read_csv(args.csv)
    if len(df) < 20:
        raise SystemExit("최소 20행 이상을 권장합니다.")

    y = df["target"].astype(float)
    X = df.drop(columns=["target"])
    cats = [c for c in ["purpose"] if c in X]
    nums = [c for c in X.columns if c not in cats]

    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cats),
        ("num", StandardScaler(), nums),
    ])
    model = Pipeline([
        ("preprocess", pre),
        ("regressor", HistGradientBoostingRegressor(max_depth=4, learning_rate=0.06)),
    ])
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42)
    model.fit(Xtr, ytr)
    print("MAE:", mean_absolute_error(yte, model.predict(Xte)))
    out = Path(__file__).resolve().parents[1] / "models" / "ranker.joblib"
    joblib.dump(model, out)
    print("saved:", out)

if __name__ == "__main__":
    main()
