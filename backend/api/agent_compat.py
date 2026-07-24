"""Small compatibility helpers for independently deployed Agent versions."""

from __future__ import annotations

import inspect
from typing import Any, Callable


def supported_kwargs(agent_method: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only keyword arguments accepted by an Agent method.

    The API and Agent may be deployed a little apart during local demos.  New
    request fields should therefore be omitted when an older method has no
    matching parameter, while methods that expose ``**kwargs`` receive the
    complete contract.  If a callable has no inspectable signature, preserve
    the old behavior and let its own invocation decide.
    """
    try:
        parameters = inspect.signature(agent_method).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return kwargs
    accepted = {
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in accepted}
