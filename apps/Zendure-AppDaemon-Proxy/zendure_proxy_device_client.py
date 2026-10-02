# SPDX-License-Identifier: GPL-3.0-only
"""Serialize HTTP operations for each physical Zendure device."""
from __future__ import annotations

import asyncio
import contextlib
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from zendure_proxy_health import post_failure_response


def build_device_url(ip: str, endpoint: str, local_proxy_url: str = "") -> str:
    endpoint = endpoint.strip("/")
    host = ip.strip()
    if host.lower().startswith("testdevice") and local_proxy_url:
        proxy = urlsplit(local_proxy_url if "://" in local_proxy_url else "http://" + local_proxy_url)
        base = proxy.path.rstrip("/")
        for suffix in ("/properties/report", "/properties/write"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        return urlunsplit((proxy.scheme, proxy.netloc, f"{base}/{quote(host, safe='')}/{endpoint}", "", ""))
    target = urlsplit(host if "://" in host else "http://" + host)
    if local_proxy_url:
        proxy = urlsplit(local_proxy_url if "://" in local_proxy_url else "http://" + local_proxy_url)
        if (target.hostname, target.port or 80) == (proxy.hostname, proxy.port or 80):
            raise ValueError("Device address points to the proxy listener")
    return urlunsplit((target.scheme, target.netloc, f"{target.path.rstrip('/')}/{endpoint}", "", ""))


def _exception_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


@dataclass
class DeviceRequest:
    method: str
    payload: Optional[dict]
    future: asyncio.Future


class DeviceClient:
    def __init__(self, ip: str, logger: Callable, metrics=None, device_idx: int = 0,
                 request_timeout: float = 60.0, separate_get_post_connections: bool = True,
                 idle_connection_close_seconds: float = 600.0):
        self.ip = ip
        self.logger = logger
        self.metrics = metrics
        self.device_idx = device_idx
        self.request_timeout = request_timeout
        self.separate_get_post_connections = separate_get_post_connections
        self.idle_connection_close_seconds = idle_connection_close_seconds
        self._local_proxy_url = ""
        self._closed = False
        self._dispatch: asyncio.Queue[DeviceRequest] = asyncio.Queue()
        get_queue: asyncio.Queue[DeviceRequest] = asyncio.Queue()
        self._queues = {"GET": get_queue, "POST": asyncio.Queue() if separate_get_post_connections else get_queue}
        keys = ("GET", "POST") if separate_get_post_connections else ("SHARED",)
        self._sessions = {key: self._create_session() for key in keys}
        self._last_activity_ts = {key: time.monotonic() for key in keys}
        self._active_requests = {key: 0 for key in keys}
        self._session_lock = asyncio.Lock()
        self._worker_task = asyncio.create_task(self._worker(self._dispatch))
        self._idle_task = asyncio.create_task(self._idle_session_cleanup()) if idle_connection_close_seconds > 0 else None

    async def get(self):
        return await self._enqueue("GET", None)

    async def post(self, payload: dict):
        return await self._enqueue("POST", payload)

    def set_local_proxy_url(self, local_proxy_url: str):
        self._local_proxy_url = local_proxy_url

    async def _enqueue(self, method: str, payload: Optional[dict]):
        if self._closed:
            return None if method == "GET" else post_failure_response("Device client is closed")
        future = asyncio.get_running_loop().create_future()
        request = DeviceRequest(method, deepcopy(payload), future)
        self._queues[method].put_nowait(request)
        self._dispatch.put_nowait(request)
        self._set_queue_depth()
        return await future

    async def _worker(self, queue: asyncio.Queue[DeviceRequest]):
        while True:
            request = await queue.get()
            self._queues[request.method].get_nowait()
            self._queues[request.method].task_done()
            self._set_queue_depth()
            try:
                if request.future.done():
                    continue
                result = await (self._execute_get() if request.method == "GET" else self._execute_post(request.payload or {}))
                if not request.future.done():
                    request.future.set_result(result)
            except asyncio.CancelledError:
                if not request.future.done():
                    request.future.cancel()
                raise
            except Exception as exc:
                self.logger(f"Device {self.ip} worker error: {_exception_message(exc)}", level="WARNING")
                if not request.future.done():
                    request.future.set_result(None if request.method == "GET" else post_failure_response(_exception_message(exc)))
            finally:
                queue.task_done()

    async def _execute_get(self):
        return await self._http_request("GET", None)

    async def _execute_post(self, payload: dict):
        return await self._http_request("POST", payload)

    async def _http_request(self, method: str, payload: Optional[dict]):
        started = time.monotonic()
        success = False
        timed_out = False
        if self.metrics:
            self.metrics.start_outgoing(self.device_idx, method)
        try:
            session = await self._start_request(method)
            endpoint = "properties/report" if method == "GET" else "properties/write"
            url = build_device_url(self.ip, endpoint, self._local_proxy_url)
            request = session.get(url) if method == "GET" else session.post(url, json=payload)
            async with request as response:
                if response.status >= 400:
                    self.logger(f"Device {self.ip} {method} HTTP {response.status}", level="WARNING")
                    return None if method == "GET" else post_failure_response(f"POST HTTP {response.status}")
                result = await response.json(content_type=None)
                success = isinstance(result, dict)
                if not success:
                    return None if method == "GET" else post_failure_response("POST returned no response")
                return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            timed_out = isinstance(exc, (TimeoutError, asyncio.TimeoutError))
            self.logger(f"Device {self.ip} {method} error: {_exception_message(exc)}", level="WARNING")
            error = "POST returned no response" if timed_out else _exception_message(exc)
            return None if method == "GET" else post_failure_response(error)
        finally:
            await self._finish_request(method)
            if self.metrics:
                self.metrics.finish_outgoing(self.device_idx, method, (time.monotonic() - started) * 1000, success, timed_out)

    async def close_post_connection(self):
        await self._close_session(self._session_key("POST"))

    async def close_get_connection(self):
        await self._close_session(self._session_key("GET"))

    async def close_idle_connections(self, current_ts: float | None = None):
        if self.idle_connection_close_seconds <= 0:
            return
        ts = time.monotonic() if current_ts is None else current_ts
        for key in self._sessions:
            if ts - self._last_activity_ts[key] >= self.idle_connection_close_seconds:
                await self._close_session(key)

    async def close(self):
        self._closed = True
        tasks = [self._worker_task] + ([self._idle_task] if self._idle_task else [])
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        while not self._dispatch.empty():
            request = self._dispatch.get_nowait()
            if not request.future.done():
                request.future.cancel()
            self._dispatch.task_done()
        for queue in self._unique_queues():
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
        self._set_queue_depth()
        for key in self._sessions:
            await self._close_session(key, force=True)

    def _set_queue_depth(self):
        if self.metrics:
            self.metrics.set_outgoing_queue_depth(self.device_idx, self._dispatch.qsize())

    def _session_key(self, method: str):
        return method if self.separate_get_post_connections else "SHARED"

    def _create_session(self):
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                                     connector=aiohttp.TCPConnector(limit=1))

    async def _start_request(self, method: str):
        key = self._session_key(method)
        async with self._session_lock:
            session = self._sessions[key]
            if session is None or session.closed:
                session = self._create_session()
                self._sessions[key] = session
            self._active_requests[key] += 1
            self._last_activity_ts[key] = time.monotonic()
            return session

    async def _finish_request(self, method: str):
        key = self._session_key(method)
        async with self._session_lock:
            self._active_requests[key] = max(0, self._active_requests[key] - 1)
            self._last_activity_ts[key] = time.monotonic()

    async def _close_session(self, key: str, *, force: bool = False):
        async with self._session_lock:
            if self._active_requests[key] and not force:
                return
            session = self._sessions[key]
            self._sessions[key] = None
            if session is not None and not session.closed:
                await session.close()

    async def _idle_session_cleanup(self):
        while True:
            await asyncio.sleep(min(60.0, max(1.0, self.idle_connection_close_seconds / 2)))
            await self.close_idle_connections()

    def _unique_queues(self):
        return list({id(queue): queue for queue in self._queues.values()}.values())
