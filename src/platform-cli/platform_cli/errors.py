"""One decorator, applied to every command, instead of a try/except block
repeated in each of them — PlatformAPIError -> a readable stderr message
and exit code 1, not a raw traceback a CLI user shouldn't have to read.

functools.wraps matters here for a reason beyond the usual "preserve
__name__/__doc__": Typer builds each command's CLI signature (its options
and arguments) via inspect.signature() on the decorated function, and
inspect.signature() only sees through a wrapper to the original function
if __wrapped__ is set — which functools.wraps sets automatically. Skip it
and every `--flag` this decorator sits in front of silently disappears
from `--help` and stops being accepted at all, not an error you'd notice
until you tried to use one.
"""
from __future__ import annotations

import functools
from collections.abc import Callable

import typer
from platform_sdk import PlatformAPIError


def handle_api_errors[F: Callable](func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except PlatformAPIError as exc:
            message = f"catalog-service error ({exc.status_code}): {exc.detail}"
            typer.secho(message, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

    return wrapper  # type: ignore[return-value]
