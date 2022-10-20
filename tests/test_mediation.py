import logging
from math import isclose
from typing import Callable, TypeVar

import pyro
import pyro.distributions as dist
import pytest
import torch

from causal_pyro.counterfactual.handlers import (
    MultiWorldCounterfactual,
    TwinWorldCounterfactual,
)
from causal_pyro.query.do_messenger import DoMessenger, do

logger = logging.getLogger(__name__)

T = TypeVar("T")

x_cf_values = [-1.0, 0.0, 2.0, 2]


def make_mediation_model(f_W: Callable, f_X: Callable, f_Z: Callable, f_Y: Callable):

    # Shared model across multiple queries/tests.
    # See Figure 1a in https://ftp.cs.ucla.edu/pub/stat_ser/R273-U.pdf

    def model():
        U1 = pyro.sample("U1", dist.Normal(0, 1))
        U2 = pyro.sample("U2", dist.Normal(0, 1))
        U3 = pyro.sample("U3", dist.Normal(0, 1))
        U4 = pyro.sample("U4", dist.Normal(0, 1))

        e_W = pyro.sample("e_W", dist.Normal(0, 1))
        W = pyro.deterministic("W", f_W(U2, U3, e_W), event_dim=0)

        e_X = pyro.sample("e_X", dist.Normal(0, 1))
        X = pyro.deterministic("X", f_X(U1, U3, U4, e_X), event_dim=0)

        e_Z = pyro.sample("e_Z", dist.Normal(0, 1))
        Z = pyro.deterministic("Z", f_Z(U4, X, W, e_Z), event_dim=0)

        e_Y = pyro.sample("e_Y", dist.Normal(0, 1))
        Y = pyro.deterministic("Y", f_Y(X, Z, U1, U2, e_Y), event_dim=0)
        return W, X, Z, Y

    return model


def linear_fs():
    def f_W(U2: T, U3: T, e_W: T) -> T:
        return U2 + U3 + e_W

    def f_X(U1: T, U3: T, U4: T, e_X: T) -> T:
        return U1 + U3 + U4 + e_X

    def f_Z(U4: T, X: T, W: T, e_X: T) -> T:
        return U4 + X + W + e_X

    def f_Y(X: T, Z: T, U1: T, U2: T, e_Y: T) -> T:
        return X + Z + U1 + U2 + e_Y

    return f_W, f_X, f_Z, f_Y


@pytest.mark.parametrize("x_cf_value", x_cf_values)
def test_do_api(x_cf_value):
    model = make_mediation_model(*linear_fs())

    # These APIs should be equivalent
    intervened_model_1 = DoMessenger({"X": x_cf_value})(model)
    intervened_model_2 = do(model, {"X": x_cf_value})

    W_1, X_1, Z_1, Y_1 = TwinWorldCounterfactual(-1)(intervened_model_1)()
    W_2, X_2, Z_2, Y_2 = TwinWorldCounterfactual(-1)(intervened_model_2)()

    assert W_1.shape == W_2.shape == torch.Size([])
    assert X_1.shape == X_2.shape == (2,)
    assert Z_1.shape == Z_2.shape == (2,)
    assert Y_1.shape == Y_2.shape == (2,)

    # Checking equality on each element is probably overkill, but may be nice for debugging tests later...
    assert W_1 != W_2
    assert X_1[0] != X_2[0]  # Sampled with fresh randomness each time
    assert X_1[1] == X_2[1]  # Intervention assignment should be equal
    assert Z_1[0] != Z_2[0]  # Sampled with fresh randomness each time
    assert Z_1[1] != Z_2[1]  # Counterfactual, but with different exogenous noise
    assert Y_1[0] != Y_2[0]  # Sampled with fresh randomness each time
    assert Y_1[1] != Y_2[1]  # Counterfactual, but with different exogenous noise


@pytest.mark.parametrize("x_cf_value", x_cf_values)
def test_linear_mediation_unconditioned(x_cf_value):

    model = make_mediation_model(*linear_fs())

    intervened_model = do(model, {"X": x_cf_value})

    with TwinWorldCounterfactual(-1):
        W, X, Z, Y = intervened_model()

    # Noise should be shared between factual and counterfactual outcomes
    # Some numerical precision issues getting these exactly equal
    assert isclose((Z - X - W)[0], (Z - X - W)[1], abs_tol=1e-5)
    assert isclose((Y - Z - X - W)[0], (Y - Z - X - W)[1], abs_tol=1e-5)


@pytest.mark.parametrize("x_cf_value", x_cf_values)
def test_linear_mediation_conditioned(x_cf_value):
    model = make_mediation_model(*linear_fs())
    x_cond_value = 0.1
    conditioned_model = pyro.condition(
        model, {"W": 1.0, "X": x_cond_value, "Z": 2.0, "Y": 1.1}
    )

    intervened_model = do(conditioned_model, {"X": x_cf_value})

    with TwinWorldCounterfactual(-1):
        W, X, Z, Y = intervened_model()

    assert X[0] == x_cond_value
    assert X[1] == x_cf_value


@pytest.mark.parametrize("x_cf_value", x_cf_values)
def test_multiworld_handler(x_cf_value):
    model = make_mediation_model(*linear_fs())

    intervened_model = do(model, {"X": x_cf_value})

    with TwinWorldCounterfactual(-1):
        W_1, X_1, Z_1, Y_1 = intervened_model()

    with MultiWorldCounterfactual(-1):
        W_2, X_2, Z_2, Y_2 = intervened_model()

    # Copied from above test.
    # TODO: refactor this to remove duplicate code.
    assert W_1.shape == W_2.shape == torch.Size([])
    assert X_1.shape == X_2.shape == (2,)
    assert Z_1.shape == Z_2.shape == (2,)
    assert Y_1.shape == Y_2.shape == (2,)

    # Checking equality on each element is probably overkill, but may be nice for debugging tests later...
    assert W_1 != W_2
    assert X_1[0] != X_2[0]  # Sampled with fresh randomness each time
    assert X_1[1] == X_2[1]  # Intervention assignment should be equal
    assert Z_1[0] != Z_2[0]  # Sampled with fresh randomness each time
    assert Z_1[1] != Z_2[1]  # Counterfactual, but with different exogenous noise
    assert Y_1[0] != Y_2[0]  # Sampled with fresh randomness each time
    assert Y_1[1] != Y_2[1]  # Counterfactual, but with different exogenous noise


@pytest.mark.parametrize("x_cf_value", [0.0])
def test_multiple_interventions(x_cf_value):
    model = make_mediation_model(*linear_fs())

    intervened_model = do(model, {"X": x_cf_value})
    intervened_model = do(intervened_model, {"Z": x_cf_value + 1.0})

    with MultiWorldCounterfactual(-1):
        W, X, Z, Y = intervened_model()

    assert W.shape == ()
    assert X.shape == (2,)
    assert Z.shape == (2, 2)
    assert Y.shape == (2, 2)


def test_mediation_nde_smoke():

    model = make_mediation_model(*linear_fs())

    # natural direct effect: DE{x,x'}(Y) = E[ Y(X=x', Z(X=x)) - E[Y(X=x)] ]
    def direct_effect(model, x, x_prime, w_obs, x_obs, z_obs, y_obs) -> Callable:
        return do(actions={"X": x})(
            do(actions={"X": x_prime})(
                do(actions={"Z": lambda Z: Z})(
                    pyro.condition(
                        data={"W": w_obs, "X": x_obs, "Z": z_obs, "Y": y_obs}
                    )(
                        MultiWorldCounterfactual(-2)(
                            pyro.plate("data", size=y_obs.shape[-1], dim=-1)(model)
                        )
                    )
                )
            )
        )

    x = torch.full((100,), 0.5)
    x_prime = torch.full((100,), 1.5)

    w_obs = torch.randn(100)
    x_obs = torch.randn(100)
    z_obs = torch.randn(100)
    y_obs = torch.randn(100)

    extended_model = direct_effect(model, x, x_prime, w_obs, x_obs, z_obs, y_obs)

    with MultiWorldCounterfactual(-2):
        W, X, Z, Y = extended_model()

    assert Y.shape == (2, 2, 2, y_obs.shape[0])


@pytest.mark.parametrize("cf_dim", [-1, -2, -3])
@pytest.mark.parametrize("event_shape", [(), (3,), (4, 3)])
def test_nested_interventions_same_variable(cf_dim, event_shape):
    def model():
        x = pyro.sample(
            "x", dist.Normal(0, 1).expand(event_shape).to_event(len(event_shape))
        )
        y = pyro.sample("y", dist.Normal(x, 1).to_event(len(event_shape)))
        return x, y

    intervened_model = do(model, {"x": torch.full(event_shape, 2.0)})
    intervened_model = do(intervened_model, {"x": torch.full(event_shape, 1.0)})

    with MultiWorldCounterfactual(cf_dim):
        x, y = intervened_model()

    assert y.shape == x.shape == (2, 2) + (1,) * (-cf_dim - 1) + event_shape
    assert torch.all(x[0, 0, ...] != 2.0) and torch.all(x[0, 0] != 1.0)
    assert torch.all(x[0, 1, ...] == 1.0)
    assert torch.all(x[1, 0, ...] == 2.0) and torch.all(x[1, 1, ...] == 2.0)
