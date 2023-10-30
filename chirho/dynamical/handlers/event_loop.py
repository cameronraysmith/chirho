from __future__ import annotations

from typing import Generic, TypeVar

import pyro

from chirho.dynamical.handlers.interruption import Interruption
from chirho.dynamical.internals.solver import (
    apply_interruptions,
    get_next_interruptions,
    get_solver,
    simulate_to_interruption,
)

S = TypeVar("S")
T = TypeVar("T")


class InterruptionEventLoop(Generic[T], pyro.poutine.messenger.Messenger):
    def _pyro_simulate(self, msg) -> None:
        dynamics, state, start_time, end_time = msg["args"]
        if msg["kwargs"].get("solver", None) is not None:
            solver = msg["kwargs"]["solver"]
        else:
            solver = get_solver()

        # Simulate through the timespan, stopping at each interruption. This gives e.g. intervention handlers
        #  a chance to modify the state and/or dynamics before the next span is simulated.
        while start_time < end_time:
            with pyro.poutine.messenger.block_messengers(
                lambda m: m is self or (isinstance(m, Interruption) and m.used)
            ):
                terminal_interruptions, interruption_time = get_next_interruptions(
                    solver, dynamics, state, start_time, end_time
                )

                state = simulate_to_interruption(
                    solver,
                    dynamics,
                    state,
                    start_time,
                    interruption_time,
                )
                start_time = interruption_time
                for h in terminal_interruptions:
                    h.used = True

            with pyro.poutine.messenger.block_messengers(
                lambda m: isinstance(m, Interruption)
                and m not in terminal_interruptions
            ):
                dynamics, state = apply_interruptions(dynamics, state)

        msg["value"] = state
        msg["done"] = True