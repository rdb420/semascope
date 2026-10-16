"""Creating the graph's constraints survives two worker processes doing it at once."""

from __future__ import annotations

from typing import Any

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from oss_pipeline.clients import neo4j as client


class Session:
    def __init__(self, fail: dict[int, BaseException]) -> None:
        self.fail = fail
        self.calls = 0

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, statement: str) -> Any:
        self.calls += 1
        if self.calls in self.fail:
            raise self.fail[self.calls]
        return type("Result", (), {"consume": lambda self: None})()


def _client(session: Session) -> client.GraphClient:
    graph = client.GraphClient.__new__(client.GraphClient)
    graph._driver = type("Driver", (), {"session": lambda self: session})()
    return graph


def _clash(code: str) -> ClientError:
    exc = ClientError()
    exc._neo4j_code = code
    return exc


def test_an_index_the_other_process_just_created_is_fine() -> None:
    session = Session({8: _clash("Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists")})
    graph = _client(session)
    graph.ensure_constraints()
    assert session.calls == len(client.CONSTRAINTS) + 2
    graph.ensure_constraints()  # done once per process
    assert session.calls == len(client.CONSTRAINTS) + 2


def test_constraints_are_tried_again_after_neo4j_was_away() -> None:
    session = Session({1: ServiceUnavailable("away")})
    graph = _client(session)
    with pytest.raises(ServiceUnavailable):
        graph.ensure_constraints()
    graph.ensure_constraints()
    assert session.calls == 1 + len(client.CONSTRAINTS) + 2


def test_other_client_errors_still_raise() -> None:
    graph = _client(Session({1: _clash("Neo.ClientError.Security.Unauthorized")}))
    with pytest.raises(ClientError):
        graph.ensure_constraints()
