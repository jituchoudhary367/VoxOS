"""Database engine configuration regressions."""

from __future__ import annotations

from types import SimpleNamespace


def test_debug_mode_does_not_echo_every_sql_statement(monkeypatch) -> None:
    from voxos.database import engine as engine_module

    captured = {}
    fake_engine = object()

    def create_engine(_url, **kwargs):
        captured.update(kwargs)
        return fake_engine

    monkeypatch.setattr(engine_module, "_engine", None)
    monkeypatch.setattr(engine_module, "create_async_engine", create_engine)
    monkeypatch.setattr(
        engine_module,
        "get_settings",
        lambda: SimpleNamespace(
            debug=True,
            db=SimpleNamespace(url="postgresql+asyncpg://user:password@example.test/voxos"),
        ),
    )

    assert engine_module.get_engine() is fake_engine
    assert captured["echo"] is False
