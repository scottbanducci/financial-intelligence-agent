"""Reproducible, opt-in classical ML evaluation on normalized dated observations.

Training is chronological, with an explicit purging gap to prevent a forecast
horizon's target rows from leaking into the validation period. A strong baseline
is always reported. All estimators run locally; no external LLM/service needed.
These are evaluation tools, not trained financial risk models.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping
from math import sqrt
import statistics

from financial_intelligence_agent.engine.operators import Series, day, dec

REGRESSORS={
    "last_value", "historical_mean", "ridge", "lasso", "elastic_net",
    "huber", "random_forest", "extra_trees", "decision_tree",
    "gradient_boosting", "hist_gradient_boosting", "knn", "svr",
}
CLASSIFIERS={
    "majority_class", "logistic_regression", "random_forest_classifier",
    "extra_trees_classifier", "gradient_boosting_classifier", "knn_classifier",
    "naive_bayes",
}
UNSUPERVISED={"isolation_forest","pca","kmeans","dbscan"}
ALGORITHMS=tuple(sorted(REGRESSORS|CLASSIFIERS|UNSUPERVISED))
SEED=1729


@dataclass(frozen=True)
class Dataset:
    dates: tuple[date,...]
    features: tuple[tuple[float,...],...]
    targets: tuple[float,...]
    feature_names: tuple[str,...]
    horizon: int
    metadata: Mapping[str,str]
    label_dates: tuple[date,...] = ()

    def __post_init__(self):
        n=len(self.dates)
        if n!=len(self.features) or n!=len(self.targets) or n<10 or (self.label_dates and n!=len(self.label_dates)):
            raise ValueError("At least 10 complete aligned observations required")
        if any(self.dates[i]>=self.dates[i+1] for i in range(n-1)):
            raise ValueError("Dates must be strictly increasing")
        if not 1<=self.horizon<=120:
            raise ValueError("Invalid forecast horizon")
        if len(self.feature_names)<1 or len(self.feature_names)>50:
            raise ValueError("Requires 1..50 features")


def make_supervised(features: Mapping[str,Series], target: Series, horizon: int = 1) -> Dataset:
    """Predict target at observation index t+h using features observed at t.

    This cannot claim point-in-time validity if provider availability metadata
    is absent; that limitation is recorded and always disclosed in evaluation.
    """
    if type(horizon) is not int or not 1<=horizon<=120:
        raise ValueError("Horizon must be integer in 1..120")
    if not features or len(features)>50:
        raise ValueError("1..50 feature series required")
    ordered=dict(sorted(features.items()))
    if len(set(ordered))!=len(ordered):
        raise ValueError("Duplicate feature name")
    if any(s.frequency!=target.frequency for s in ordered.values()):
        raise ValueError("Mixed frequencies require explicit resampling")
    if any(s.convention!=target.convention for s in ordered.values()):
        raise ValueError("Mismatched observation conventions")
    target_points={p.date:p for p in target.select().points}
    feature_points={name:{p.date:p for p in s.select().points} for name,s in ordered.items()}
    dates=sorted(set(target_points).intersection(*(set(points) for points in feature_points.values())))
    result_dates=[]; label_dates=[]; X=[]; y=[]
    for i in range(len(dates)-horizon):
        feature_date=dates[i]
        label_date=dates[i+horizon]
        feature_values=[feature_points[name][feature_date].value for name in ordered]
        target_value=target_points[label_date].value
        if target_value is None or any(v is None for v in feature_values):
            continue
        result_dates.append(feature_date)
        label_dates.append(label_date)
        X.append(tuple(float(v) for v in feature_values))
        y.append(float(target_value))
    # Fixed horizon is measured in common-observation steps, not calendar days.
    coverage = all(p.available_at is not None for s in list(ordered.values())+[target]
                   for p in s.points)
    return Dataset(tuple(result_dates),tuple(X),tuple(y),tuple(ordered),horizon,{
        "frequency":target.frequency,
        "target_key":target.key,
        "as_of_claim":"availability_verified" if coverage else "not_point_in_time_verified",
        "horizon_unit":"aligned_observations",
    },tuple(label_dates))


def _model(name: str, task: str):
    """Instantiated with explicit seed and bounded defaults (no arbitrary class)."""
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge,Lasso,ElasticNet,HuberRegressor,LogisticRegression
    from sklearn.ensemble import (RandomForestRegressor,ExtraTreesRegressor,GradientBoostingRegressor,
                                  HistGradientBoostingRegressor,RandomForestClassifier,
                                  ExtraTreesClassifier,GradientBoostingClassifier)
    from sklearn.tree import DecisionTreeRegressor
    from sklearn.neighbors import KNeighborsRegressor,KNeighborsClassifier
    from sklearn.svm import SVR
    from sklearn.naive_bayes import GaussianNB
    options={
        "ridge":Ridge(alpha=1.0),
        "lasso":Lasso(alpha=0.05,max_iter=10000),
        "elastic_net":ElasticNet(alpha=0.1,l1_ratio=0.5,max_iter=10000),
        "huber":HuberRegressor(max_iter=500),
        "random_forest":RandomForestRegressor(n_estimators=80,max_depth=6,n_jobs=1,random_state=SEED),
        "extra_trees":ExtraTreesRegressor(n_estimators=80,max_depth=6,n_jobs=1,random_state=SEED),
        "decision_tree":DecisionTreeRegressor(max_depth=5,random_state=SEED),
        "gradient_boosting":GradientBoostingRegressor(n_estimators=80,max_depth=3,random_state=SEED),
        "hist_gradient_boosting":HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=15,random_state=SEED),
        "knn":KNeighborsRegressor(n_neighbors=3),
        "svr":SVR(C=1.0,epsilon=0.1),
        "logistic_regression":LogisticRegression(max_iter=1000,random_state=SEED),
        "random_forest_classifier":RandomForestClassifier(n_estimators=80,max_depth=6,n_jobs=1,random_state=SEED),
        "extra_trees_classifier":ExtraTreesClassifier(n_estimators=80,max_depth=6,n_jobs=1,random_state=SEED),
        "gradient_boosting_classifier":GradientBoostingClassifier(n_estimators=80,random_state=SEED),
        "knn_classifier":KNeighborsClassifier(n_neighbors=3),
        "naive_bayes":GaussianNB(),
    }
    if name not in options:
        raise ValueError("Unsupported supervised estimator")
    return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),options[name])


def walk_forward(dataset: Dataset, *, algorithm: str, task: str = "regression",
                 initial_train: int = 20, test_size: int = 5, folds: int = 3,
                 embargo: int | None = None) -> dict[str,Any]:
    """Expanding-window validation, with deterministic fold boundaries.

    The embargo (minimum horizon) separates the end of training features and
    the start of test features. A training label at t+h must not overlap any
    test-feature date. Leakage from revised source values remains an external
    provenance constraint, never concealed.
    """
    if task not in {"regression","classification"}:
        raise ValueError("Only supervised regression/classification supported")
    accepted=REGRESSORS if task=="regression" else CLASSIFIERS
    if algorithm not in accepted:
        raise ValueError("Algorithm not registered for requested task")
    if any(type(v) is not int for v in (initial_train,test_size,folds)):
        raise ValueError("Split parameters must be integers")
    if initial_train<10 or not 1<=test_size<=100 or not 1<=folds<=10:
        raise ValueError("Split outside safety bounds")
    gap=dataset.horizon if embargo is None else embargo
    if type(gap) is not int or not dataset.horizon<=gap<=120:
        raise ValueError("Embargo must be >= target horizon")
    n=len(dataset.dates)
    if initial_train+gap+folds*test_size>n:
        raise ValueError("Insufficient rows for requested purged folds")
    if task=="classification" and any(y!=int(y) for y in dataset.targets):
        raise ValueError("Classification targets must be integer-coded categories")
    fold_results=[]; all_true=[]; all_pred=[]; baseline_pred=[]
    for fold in range(folds):
        train_stop=initial_train+fold*test_size
        test_start=train_stop+gap
        test_stop=test_start+test_size
        # Purge training labels whose actual target dates reach into the test
        # feature window. This handles holes in features/targets, where a fixed
        # row gap alone is insufficient to prevent overlap.
        first_test=dataset.dates[test_start]
        allowed=[i for i in range(train_stop)
                 if not dataset.label_dates or dataset.label_dates[i] < first_test]
        if len(allowed)<10:
            raise ValueError("Purged training set has fewer than 10 rows")
        Xtrain=[dataset.features[i] for i in allowed]
        ytrain=[dataset.targets[i] for i in allowed]
        Xtest=list(dataset.features[test_start:test_stop]); ytest=list(dataset.targets[test_start:test_stop])
        if algorithm in {"last_value","historical_mean","majority_class"}:
            if algorithm=="last_value":
                prediction=[ytrain[-1]]*len(ytest)
            elif algorithm=="historical_mean":
                prediction=[statistics.mean(ytrain)]*len(ytest)
            else:
                prediction=[max(sorted(set(ytrain)),key=ytrain.count)]*len(ytest)
        else:
            if task=="classification" and len(set(ytrain))<2:
                raise ValueError("Fold has fewer than two training classes")
            model=_model(algorithm,task)
            model.fit(Xtrain,ytrain)
            prediction=[float(v) for v in model.predict(Xtest)]
        if task=="classification":
            baseline=[float(max(sorted(set(ytrain)),key=ytrain.count))]*len(ytest)
        else:
            baseline=[statistics.mean(ytrain)]*len(ytest)
        all_true+=ytest; all_pred+=prediction; baseline_pred+=baseline
        fold_results.append({
            "fold":fold+1,
            "train_start":dataset.dates[0].isoformat(),
            "train_last_feature_date":dataset.dates[train_stop-1].isoformat(),
            "test_start":dataset.dates[test_start].isoformat(),
            "test_end":dataset.dates[test_stop-1].isoformat(),
            "train_rows":len(ytrain),"test_rows":len(ytest),"embargo_rows":gap,
            "purged_training_rows":train_stop-len(allowed),
            "train_last_label_date":max(dataset.label_dates[i] for i in allowed).isoformat() if dataset.label_dates else None,
        })
    def metrics(y,pred):
        if task=="classification":
            return {"accuracy":sum(a==b for a,b in zip(y,pred))/len(y),
                    "n_test":len(y)}
        errors=[a-b for a,b in zip(y,pred)]
        mean=statistics.mean(y)
        tss=sum((a-mean)**2 for a in y)
        return {"mae":sum(abs(v) for v in errors)/len(y),
                "rmse":sqrt(sum(v*v for v in errors)/len(y)),
                "r2":1-sum(v*v for v in errors)/tss if tss else None,
                "n_test":len(y)}
    return {
        "algorithm":algorithm,"task":task,"seed":SEED,
        "dataset_metadata":dict(dataset.metadata),
        "splits":fold_results,
        "model_metrics":metrics(all_true,all_pred),
        "baseline_metrics":metrics(all_true,baseline_pred),
        "warnings":[
            "Evaluated only on ordered frozen historical observations; not a production forecast.",
            "Historical revisions/publication timestamps must be validated before point-in-time inference.",
            "Hyperparameters are fixed; no test-set fitting or parameter search.",
            "A horizon is a number of aligned observations, not calendar days.",
        ],
    }


def unsupervised_analysis(dataset: Dataset, *, algorithm: str, n_clusters: int = 3) -> dict[str,Any]:
    """Bounded unsupervised research methods; fit does not use target values.

    Unsupervised fitting on the entire historical fixture is descriptive, not an
    out-of-sample anomaly detector or independently validated risk predictor.
    """
    if algorithm not in UNSUPERVISED:
        raise ValueError("Unregistered unsupervised algorithm")
    if type(n_clusters) is not int or not 2<=n_clusters<=8:
        raise ValueError("Cluster count must be in [2,8]")
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans,DBSCAN
    from sklearn.ensemble import IsolationForest
    import numpy as np
    if len(dataset.features)>10000:
        raise ValueError("Sample limit exceeded")
    X=StandardScaler().fit_transform(np.asarray(dataset.features,dtype=float))
    if algorithm=="pca":
        model=PCA(n_components=min(2,X.shape[1],len(X)),svd_solver="full")
        transformed=model.fit_transform(X)
        return {
            "algorithm":algorithm,"observations":len(X),
            "explained_variance_ratio":[float(v) for v in model.explained_variance_ratio_],
            "first_rows":transformed[:20].tolist(),
            "feature_names":dataset.feature_names,
            "warning":"Descriptive in-sample component analysis; no predictive claim.",
        }
    if algorithm=="kmeans":
        labels=KMeans(n_clusters=n_clusters,random_state=SEED,n_init=10).fit_predict(X)
    elif algorithm=="dbscan":
        labels=DBSCAN(eps=0.75,min_samples=5).fit_predict(X)
    else:
        labels=IsolationForest(n_estimators=80,contamination=0.08,random_state=SEED,n_jobs=1).fit_predict(X)
    return {
        "algorithm":algorithm,"observations":len(X),
        "label_counts":{str(int(k)):int(sum(labels==k)) for k in sorted(set(labels))},
        "first_labels":[int(v) for v in labels[:20]],
        "warning":"In-sample descriptive clusters/anomalies; no return prediction or live alert.",
    }
