"""HTTP and CLI application for serving cashback snapshots."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Annotated, Callable, Mapping, Sequence, TypeVar

from fastapi import Body, FastAPI, HTTPException, Path as PathParameter, Response
from pydantic import StrictStr
import uvicorn

from scripts import cashbacks
from . import models, repo_worker


REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
FORBIDDEN_REF_CHARACTERS = frozenset(" ~^:?*[\\")
CompanyId = Annotated[int, PathParameter(gt=0)]
MatchCategories = Annotated[list[StrictStr], Body(min_length=1)]



def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 65535") from exc
    if not 0 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 65535")
    return parsed


def _valid_remote_name(value: str) -> str:
    if not REMOTE_NAME.fullmatch(value) or value.endswith(".lock") or ".." in value:
        raise argparse.ArgumentTypeError("must be a valid, non-option Git remote name")
    return value


def _valid_ref_name(value: str) -> str:
    if (
        not value
        or value.startswith("-")
        or value.startswith("/")
        or value.endswith(("/", "."))
        or value == "@"
        or "//" in value
        or ".." in value
        or "@{" in value
        or any(character in FORBIDDEN_REF_CHARACTERS or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise argparse.ArgumentTypeError("must be one valid, non-option branch or tag name")
    components = value.split("/")
    if any(not component or component.startswith(".") or component.endswith(".lock") for component in components):
        raise argparse.ArgumentTypeError("must be one valid, non-option branch or tag name")
    if re.fullmatch(r"[0-9a-fA-F]{7,}", value):
        raise argparse.ArgumentTypeError("commit IDs are not accepted; name a branch or tag")
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Serve validated cashback data from a Git checkout."
    )


ValidatedValue = TypeVar("ValidatedValue")


def _trimmed_environment_value(environ: Mapping[str, str], name: str) -> str:
    return environ.get(name, "").strip()


def _validated_environment_value(
    parser: argparse.ArgumentParser,
    name: str,
    value: str,
    validator: Callable[[str], ValidatedValue],
) -> ValidatedValue:
    try:
        return validator(value)
    except argparse.ArgumentTypeError as exc:
        parser.error(f"{name}: {exc}")


def parse_config(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> models.Config:
    parser = build_argument_parser()
    parser.parse_args(argv)
    values = os.environ if environ is None else environ

    checkout = _trimmed_environment_value(values, "CHECKOUT_DIR")
    if not checkout:
        parser.error("CHECKOUT_DIR must be a non-empty path")

    repository_url = _trimmed_environment_value(values, "REPOSITORY_URL")
    if not repository_url:
        parser.error("REPOSITORY_URL must be a non-empty URL or path")
    github_token = _trimmed_environment_value(values, "GITHUB_TOKEN") or None
    host = _trimmed_environment_value(values, "HOST") or "0.0.0.0"
    port = _validated_environment_value(
        parser,
        "PORT",
        _trimmed_environment_value(values, "PORT") or "8080",
        _port,
    )
    remote = _validated_environment_value(
        parser,
        "REMOTE",
        _trimmed_environment_value(values, "REMOTE") or "origin",
        _valid_remote_name,
    )
    ref = _validated_environment_value(
        parser,
        "REF",
        _trimmed_environment_value(values, "REF") or "main",
        _valid_ref_name,
    )
    git_timeout_seconds = _validated_environment_value(
        parser,
        "GIT_TIMEOUT_SECONDS",
        _trimmed_environment_value(values, "GIT_TIMEOUT_SECONDS") or "300",
        _positive_integer,
    )

    return models.Config(
        checkout=Path(checkout),
        repository_url=repository_url,
        github_token=github_token,
        host=host,
        port=port,
        remote=remote,
        ref=ref,
        git_timeout_seconds=git_timeout_seconds,
    )


def create_app(state: repo_worker.ServiceState) -> FastAPI:
    application = FastAPI()

    @application.get("/banks")
    async def banks() -> Response:
        return Response(
            content=state.snapshot().banks_body, media_type="application/json"
        )

    @application.get("/banks/{company_id}/categories")
    async def categories(company_id: CompanyId) -> Response:
        bank = state.snapshot().banks.get(company_id)
        if bank is None:
            raise HTTPException(status_code=404, detail="bank_not_found")
        return Response(content=bank.categories_body, media_type="application/json")

    @application.post("/banks/{company_id}/categories/match")
    async def match(
        company_id: CompanyId, categories: MatchCategories
    ) -> Response:
        bank = state.snapshot().banks.get(company_id)
        if bank is None:
            raise HTTPException(status_code=404, detail="bank_not_found")
        return Response(
            content=bank.match_body(categories), media_type="application/json"
        )

    @application.post("/sync")
    def sync() -> dict[str, str]:
        revision = state.sync()
        if revision is None:
            raise HTTPException(status_code=503, detail="sync_failed")
        return {"revision": revision}

    return application


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_config(argv)
    git = repo_worker.GitRunner(
        config.git_timeout_seconds, github_token=config.github_token
    )
    state: repo_worker.ServiceState | None = None
    try:
        try:
            checkout, snapshot = repo_worker.bootstrap_checkout(config, git)
            state = repo_worker.ServiceState(config, checkout, snapshot, git=git)
        except (
            repo_worker.StartupError,
            repo_worker.GitCommandError,
            cashbacks.DataError,
            OSError,
        ) as exc:
            print(f"cashbacks-service: {exc}", file=sys.stderr)
            return 1

        try:
            uvicorn.run(
                create_app(state), host=config.host, port=config.port, workers=1
            )
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"cashbacks-service: {exc}", file=sys.stderr)
            return 1
        return 0
    finally:
        if state is None:
            git.shutdown()
        else:
            state.shutdown()


if __name__ == "__main__":
    sys.exit(main())
