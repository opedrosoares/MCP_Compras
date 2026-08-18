"""Cache TTL+LRU genérico (memória) com adapter opcional para Redis.

Single-process: async-safe via asyncio.Lock, não persiste entre restarts.
Quando `REDIS_URL` está setada no ambiente, `cache_from_env` retorna um
`RedisCache` que satisfaz a mesma interface — útil para múltiplos pods no
Railway compartilharem o cache.

Métricas observáveis (hits/misses/evictions cumulativos + janelados em 1h,
started_at, uptime_seconds, config_sources).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

_WINDOW_SECONDS = 3600


@runtime_checkable
class Cache(Protocol, Generic[T]):
    """Interface comum dos caches (memória e Redis)."""

    async def get(self, key: str) -> T | None: ...
    async def set(self, key: str, value: T) -> None: ...
    async def stats(self) -> dict[str, Any]: ...
    async def clear(self) -> None: ...


class ResultCache(Generic[T]):
    """Cache TTL+LRU async-safe em memória com métricas observáveis."""

    def __init__(
        self,
        ttl_seconds: float,
        max_size: int = 100,
        *,
        ttl_from_env: bool = False,
        max_size_from_env: bool = False,
        backend: str = "memory",
    ) -> None:
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._ttl_from_env = ttl_from_env
        self._max_size_from_env = max_size_from_env
        self._backend = backend
        self._store: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._hit_times: deque[float] = deque()
        self._miss_times: deque[float] = deque()
        self._started_epoch = time.time()
        self._started_monotonic = time.monotonic()

    def _trim_window_locked(self) -> None:
        cutoff = time.monotonic() - _WINDOW_SECONDS
        while self._hit_times and self._hit_times[0] < cutoff:
            self._hit_times.popleft()
        while self._miss_times and self._miss_times[0] < cutoff:
            self._miss_times.popleft()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            self._trim_window_locked()
            now = time.monotonic()
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                self._miss_times.append(now)
                return None
            ts, output = entry
            if now - ts > self.ttl:
                del self._store[key]
                self.misses += 1
                self._miss_times.append(now)
                return None
            self._store.move_to_end(key)
            self.hits += 1
            self._hit_times.append(now)
            return output

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic(), value)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)
                self.evictions += 1

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            self._trim_window_locked()
            total = self.hits + self.misses
            hit_rate = round(self.hits / total, 3) if total else 0.0
            recent_hits = len(self._hit_times)
            recent_misses = len(self._miss_times)
            recent_total = recent_hits + recent_misses
            recent_hit_rate = (
                round(recent_hits / recent_total, 3) if recent_total else 0.0
            )
            return {
                "backend": self._backend,
                "size": len(self._store),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": hit_rate,
                "hits_last_hour": recent_hits,
                "misses_last_hour": recent_misses,
                "hit_rate_last_hour": recent_hit_rate,
                "evictions": self.evictions,
                "started_at": datetime.fromtimestamp(
                    self._started_epoch, tz=UTC
                ).isoformat(),
                "uptime_seconds": int(time.monotonic() - self._started_monotonic),
                "config_sources": {
                    "ttl_seconds": "env" if self._ttl_from_env else "default",
                    "max_size": "env" if self._max_size_from_env else "default",
                },
            }

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            self._hit_times.clear()
            self._miss_times.clear()
            self._started_epoch = time.time()
            self._started_monotonic = time.monotonic()


class RedisCache(Generic[T]):
    """Adapter Redis com a mesma interface do ResultCache.

    Sem LRU explícito: confiamos no eviction policy do servidor Redis
    (geralmente `allkeys-lru`). TTL aplicado por chave via SETEX.
    Valores são serializados como JSON (precisam ser JSON-serializáveis).

    Não bloqueia o servidor: em caso de erro de conexão, devolve `None`
    no get e ignora silenciosamente no set (degrada para "sem cache").
    """

    def __init__(
        self,
        url: str,
        prefix: str,
        ttl_seconds: float,
        *,
        ttl_from_env: bool = False,
    ) -> None:
        # Import tardio: redis é opcional (só carrega se REDIS_URL setada).
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(url, decode_responses=True)
        self._prefix = prefix
        self.ttl = ttl_seconds
        self._ttl_from_env = ttl_from_env
        self.hits = 0
        self.misses = 0
        self._started_epoch = time.time()
        self._started_monotonic = time.monotonic()

    def _k(self, key: str) -> str:
        # Incluir __version__ no prefixo automaticamente invalida o cache
        # entre releases — sem isso, deploys cujo payload mudou (campos
        # novos como `_aviso_filtro`, `resumo_por_item`, `clusters`) ficam
        # servindo o JSON antigo por horas/dias até o TTL expirar.
        # Achado bateria A rodada 4 v0.3.9: Redis remoto servia payload
        # pré-v0.3.6 sem `_aviso_filtro` mesmo com servidor já em v0.3.9.
        from compras_mcp import __version__

        return f"compras:v{__version__}:{self._prefix}:{key}"

    async def get(self, key: str) -> T | None:
        try:
            raw = await self._client.get(self._k(key))
        except Exception:
            self.misses += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, key: str, value: T) -> None:
        try:
            payload = json.dumps(value, default=str, ensure_ascii=False)
            await self._client.setex(self._k(key), int(self.ttl), payload)
        except Exception:
            # Degrade silenciosamente — Redis indisponível não deve quebrar tools
            return

    async def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = round(self.hits / total, 3) if total else 0.0
        return {
            "backend": "redis",
            "prefix": self._prefix,
            "ttl_seconds": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "started_at": datetime.fromtimestamp(self._started_epoch, tz=UTC).isoformat(),
            "uptime_seconds": int(time.monotonic() - self._started_monotonic),
            "config_sources": {
                "ttl_seconds": "env" if self._ttl_from_env else "default",
            },
        }

    async def clear(self) -> None:
        try:
            cursor = 0
            pattern = f"compras:{self._prefix}:*"
            while True:
                cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    await self._client.delete(*keys)
                if cursor == 0:
                    break
            self.hits = 0
            self.misses = 0
            self._started_epoch = time.time()
            self._started_monotonic = time.monotonic()
        except Exception:
            return


def cache_from_env(
    prefix: str, *, default_ttl: int = 3600, default_max_size: int = 100
) -> Cache[Any]:
    """Cria um cache lendo TTL/max_size das env vars `CACHE_{PREFIX}_*`.

    Se `REDIS_URL` estiver setada, retorna `RedisCache`. Caso contrário,
    `ResultCache` em memória.

    Exemplos:
      - cache_from_env("CATALOGO") lê CACHE_CATALOGO_TTL e CACHE_CATALOGO_MAX_SIZE.
      - cache_from_env("PRECOS") lê CACHE_PRECOS_TTL e CACHE_PRECOS_MAX_SIZE.
    """
    ttl_key = f"CACHE_{prefix}_TTL"
    max_key = f"CACHE_{prefix}_MAX_SIZE"
    ttl = float(os.environ.get(ttl_key, str(default_ttl)))
    max_size = int(os.environ.get(max_key, str(default_max_size)))

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        return RedisCache(
            url=redis_url,
            prefix=prefix.lower(),
            ttl_seconds=ttl,
            ttl_from_env=ttl_key in os.environ,
        )
    return ResultCache(
        ttl_seconds=ttl,
        max_size=max_size,
        ttl_from_env=ttl_key in os.environ,
        max_size_from_env=max_key in os.environ,
    )
