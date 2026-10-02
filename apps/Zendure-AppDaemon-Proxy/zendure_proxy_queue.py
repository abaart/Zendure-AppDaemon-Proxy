# SPDX-License-Identifier: GPL-3.0-only
"""Batch Home Assistant callers without losing individual reply futures."""
from __future__ import annotations

import asyncio
from copy import deepcopy


def _open_futures(futures: list[asyncio.Future]) -> list[asyncio.Future]:
    return [future for future in futures if not future.done()]


def _dedup_posts(posts: list[tuple[dict, asyncio.Future]]):
    groups = {}
    for payload, future in posts:
        if future.done():
            continue
        properties = payload.get("properties", payload)
        keys = frozenset(properties) if isinstance(properties, dict) else frozenset(payload)
        if keys in groups:
            previous_payload, previous_future, skipped = groups.pop(keys)
            skipped.append(previous_future)
        else:
            skipped = []
        groups[keys] = (payload, future, skipped)
    return list(groups.values())


class RequestQueue:
    def __init__(self):
        self._gets: list[asyncio.Future] = []
        self._posts: list[tuple[dict, asyncio.Future]] = []
        self._available = asyncio.Event()
        self._closed = False

    async def enqueue_get(self):
        future = asyncio.get_running_loop().create_future()
        if self._closed:
            future.cancel()
            return future
        self._gets.append(future)
        self._available.set()
        return future

    async def enqueue_post(self, payload: dict):
        future = asyncio.get_running_loop().create_future()
        if self._closed:
            future.cancel()
            return future
        self._posts.append((deepcopy(payload), future))
        self._available.set()
        return future

    async def drain(self):
        while True:
            await self._available.wait()
            if self._closed:
                raise asyncio.CancelledError
            gets = _open_futures(self._gets)
            posts = _dedup_posts(self._posts)
            self._gets = []
            self._posts = []
            self._available.clear()
            if gets or posts:
                return gets, posts

    async def drain_gets_nowait(self):
        gets = _open_futures(self._gets)
        self._gets = []
        if not self._posts:
            self._available.clear()
        return gets

    async def depths(self):
        return len(_open_futures(self._gets)), sum(not future.done() for _, future in self._posts)

    async def close(self):
        self._closed = True
        for future in self._gets + [future for _, future in self._posts]:
            if not future.done():
                future.cancel()
        self._gets = []
        self._posts = []
        self._available.set()
