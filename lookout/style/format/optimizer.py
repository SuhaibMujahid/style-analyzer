"""Optimize base model hyper-parameters."""
from functools import partial
from logging import getLogger
from threading import Thread
from typing import Any, Mapping, Optional, Tuple

from lookout.core.slogging import logs_are_structured
import numpy
from scipy.optimize import OptimizeResult
from scipy.sparse import csr_matrix
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.tree import DecisionTreeClassifier
from skopt import gp_minimize
from skopt.space import Categorical, Integer
from skopt.utils import use_named_args


_dimensions = [
    Categorical(name="base_model_name", categories=["sklearn.ensemble.RandomForestClassifier",
                                                    "sklearn.tree.DecisionTreeClassifier"]),
    Categorical(name="max_depth", categories=[None, 5, 10]),
    Categorical(name="max_features", categories=[None, "auto"]),
    Integer(name="min_samples_split", low=2, high=20),
    Integer(name="min_samples_leaf", low=1, high=20),
]


class Optimizer:
    """Optimize base model hyper-parameters."""

    _log = getLogger("Optimizer")

    def __init__(self, cv: int, n_iter: int, n_jobs: Optional[int], random_state: int):
        """
        Construct an `Optimizer`.

        :param cv: Number of folds to use during cross-validation.
        :param n_iter: Number of optimization iterations. Minimum 10.
        :param n_jobs: Number of jobs to use. Passed on to cross_val_score.
        :param random_state: Random seed.
        """
        self.cv = cv
        if n_iter < 10:
            self._log.warning("n_iter values below 10 (%d) are considered as 10.", n_iter)
        self.n_iter = max(10, n_iter)
        self.n_jobs = n_jobs
        self.random_state = random_state

    def optimize(self, X: csr_matrix, y: numpy.ndarray) -> Tuple[float, Mapping[str, Any]]:
        """
        Conduct hyper-parameters search to find the best base model given the data.

        :param X: Sparse feature matrix.
        :param y: Labels numpy array.
        :return: Best base model score and parameters.
        """
        cost_function = use_named_args(_dimensions)(partial(self._cost, X=X, y=y))

        def _minimize() -> OptimizeResult:
            return gp_minimize(cost_function, _dimensions, n_calls=self.n_iter,
                               random_state=self.random_state, verbose=True)

        if not logs_are_structured:
            # fool the check in joblib - everything still works without it
            # this trick allows to run parallel bscv.fit()
            from unittest.mock import patch
            with patch("threading._MainThread", Thread):
                self._log.debug("patched joblib")
                res = _minimize()
        else:
            res = _minimize()
        best_score = -res.fun
        best_params = {dim.name: x for x, dim in zip(res.x, _dimensions)}
        return best_score, best_params

    def _cost(self, *, X: csr_matrix, y: numpy.ndarray, **params: Any) -> float:
        params_copy = params.copy()
        base_model_name = params_copy.pop("base_model_name")
        if base_model_name == "sklearn.tree.DecisionTreeClassifier":
            base_model_class = DecisionTreeClassifier
        elif base_model_name == "sklearn.ensemble.RandomForestClassifier":
            base_model_class = RandomForestClassifier
            params_copy["n_estimators"] = 10
            params_copy["n_jobs"] = -1
        params_copy["random_state"] = self.random_state
        base_model = base_model_class(**params_copy)
        cv = StratifiedKFold(self.cv, random_state=self.random_state)
        return -numpy.mean(cross_val_score(base_model, X, y, cv=cv, n_jobs=self.n_jobs))
