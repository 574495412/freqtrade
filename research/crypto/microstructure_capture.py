"""Bounded public futures capture with raw receive clocks and explicit epochs."""

import argparse
import asyncio
import gzip
import json
import os
import shutil
import signal
import time
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

from freqtrade.research.data import read, save, sha


def atomic_save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    save(temporary, value)
    temporary.replace(path)


class RawWriter:
    def __init__(self, output, maximum_bytes, chunk_seconds):
        self.output, self.maximum_bytes, self.chunk_seconds = output, maximum_bytes, chunk_seconds
        self.started = time.monotonic()
        self.current_slot = None
        self.stream = None
        self.path = None
        self.bytes = 0
        self.rows = 0
        self.chunk_rows = 0
        self.closed = []
        self.stack = ExitStack()

    def close(self):
        if self.stream is not None:
            self.stack.close()
            self.stream = None
            self.closed.append(
                {
                    "file": self.path.name,
                    "rows": self.chunk_rows,
                    "sha256": sha(self.path),
                    "compressed_bytes": self.path.stat().st_size,
                }
            )
            atomic_save(self.output / "chunks.json", self.closed)

    def append(self, record):
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if self.bytes + len(encoded) > self.maximum_bytes:
            raise OSError("Registered raw capture byte cap reached")
        slot = int((time.monotonic() - self.started) // self.chunk_seconds)
        if slot != self.current_slot:
            self.close()
            self.current_slot = slot
            self.path = self.output / f"raw_{slot:05d}.jsonl.gz"
            if self.path.exists():
                raise ValueError("Preserve existing raw chunk")
            # The rotating writer's ExitStack owns and closes each gzip stream.
            self.stream = self.stack.enter_context(
                gzip.open(self.path, "xb", compresslevel=3)  # noqa: SIM115
            )
            self.chunk_rows = 0
        self.stream.write(encoded)
        self.bytes += len(encoded)
        self.rows += 1
        self.chunk_rows += 1


class Capture:
    def __init__(self, plan, output, profile):
        self.plan, self.output, self.profile = plan, output, profile
        self.spec = plan["capture"]
        self.writer = RawWriter(
            output, self.spec["maximum_uncompressed_bytes"], self.spec["chunk_seconds"]
        )
        self.counts = Counter()
        self.last_received = {}
        self.epochs = Counter()
        self.stop = asyncio.Event()
        self.reason = "registered_duration_completed"
        self.error = None
        self.started_mono = time.monotonic_ns()
        self.started_wall = time.time_ns()
        self.clock_samples = []
        self.events = (output / "events.jsonl").open("x")

    def event(self, event, **fields):
        self.events.write(
            json.dumps(
                {
                    "event": event,
                    "wall_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                    **fields,
                }
            )
            + "\n"
        )
        self.events.flush()

    def status(self, terminal=False):
        return {
            "pid": os.getpid(),
            "state": "terminal" if terminal else "running",
            "profile": self.profile,
            "started_wall_ns": self.started_wall,
            "started_monotonic_ns": self.started_mono,
            "elapsed_seconds": (time.monotonic_ns() - self.started_mono) / 1e9,
            "duration_seconds": self.plan["profiles"][self.profile]["duration_seconds"],
            "counts": dict(self.counts),
            "last_received_wall_ns": self.last_received,
            "epochs": dict(self.epochs),
            "raw_rows": self.writer.rows,
            "uncompressed_bytes": self.writer.bytes,
            "closed_chunks": len(self.writer.closed),
            "stop_reason": self.reason if terminal else None,
            "error_type": self.error,
            "orders_enabled": False,
            "production_eligible": False,
        }

    async def socket(self, session, channel):
        template = self.plan["sources"]["depth_stream" if channel == "depth" else "trade_stream"]
        streams = [template.format(symbol=symbol.lower()) for symbol in self.plan["symbols"]]
        url = self.plan["sources"][channel] + "?streams=" + "/".join(streams)
        failures = 0
        while not self.stop.is_set():
            epoch = self.epochs[channel]
            self.epochs[channel] += 1
            self.event("connection_attempt", channel=channel, epoch=epoch)
            try:
                timeout = aiohttp.ClientWSTimeout(
                    ws_receive=self.spec["socket_timeout_seconds"],
                    ws_close=2,
                )
                async with session.ws_connect(url, heartbeat=15, timeout=timeout) as websocket:
                    self.event("connected", channel=channel, epoch=epoch)
                    while not self.stop.is_set():
                        message = await websocket.receive()
                        received_mono, received_wall = time.monotonic_ns(), time.time_ns()
                        if message.type != aiohttp.WSMsgType.TEXT:
                            raise ConnectionError("Websocket stopped delivering text")
                        record = {
                            "sequence": self.writer.rows,
                            "channel": channel,
                            "epoch": epoch,
                            "received_wall_ns": received_wall,
                            "received_monotonic_ns": received_mono,
                            "raw": message.data,
                        }
                        self.writer.append(record)
                        try:
                            data = json.loads(message.data)["data"]
                            symbol = data.get("s", "UNKNOWN")
                            self.counts[f"{channel}:{symbol}"] += 1
                            self.last_received[f"{channel}:{symbol}"] = received_wall
                        except (ValueError, KeyError, TypeError, AttributeError):
                            self.counts[f"{channel}:UNPARSEABLE"] += 1
            except (TimeoutError, aiohttp.ClientError, ConnectionError) as error:
                failures += 1
                self.event(
                    "disconnected", channel=channel, epoch=epoch, error_type=type(error).__name__
                )
                if failures > self.spec["maximum_reconnects_per_channel"]:
                    raise ConnectionError("Registered reconnect limit exceeded") from error
                delay = min(
                    self.spec["maximum_reconnect_delay_seconds"],
                    self.spec["reconnect_delay_seconds"] * failures,
                )
                await asyncio.sleep(delay)
            finally:
                self.event("connection_epoch_ended", channel=channel, epoch=epoch)

    async def clock(self, session):
        while not self.stop.is_set():
            sent_mono, sent_wall = time.monotonic_ns(), time.time_ns()
            try:
                async with session.get(self.plan["sources"]["clock"]) as response:
                    response.raise_for_status()
                    raw = await response.json()
                    received_mono, received_wall = time.monotonic_ns(), time.time_ns()
                    self.clock_samples.append(
                        {
                            "sent_wall_ns": sent_wall,
                            "received_wall_ns": received_wall,
                            "sent_monotonic_ns": sent_mono,
                            "received_monotonic_ns": received_mono,
                            "server_time_ms": int(raw["serverTime"]),
                        }
                    )
                    atomic_save(self.output / "clock_samples.json", self.clock_samples)
            except (TimeoutError, aiohttp.ClientError, ValueError, KeyError) as error:
                self.event("clock_probe_failed", error_type=type(error).__name__)
            await asyncio.sleep(self.spec["clock_interval_seconds"])

    async def heartbeat(self):
        while not self.stop.is_set():
            atomic_save(self.output / "status.json", self.status())
            await asyncio.sleep(self.spec["status_seconds"])

    async def run(self):
        loop = asyncio.get_running_loop()

        def stop_signal():
            self.reason = "operator_signal"
            self.stop.set()

        for signum in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(signum, stop_signal)
        timeout = aiohttp.ClientTimeout(total=15, sock_connect=12)
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            tasks = [
                asyncio.create_task(coroutine)
                for coroutine in [
                    self.socket(session, "depth"),
                    self.socket(session, "trades"),
                    self.clock(session),
                    self.heartbeat(),
                ]
            ]
            stop_task = asyncio.create_task(self.stop.wait())
            duration = self.plan["profiles"][self.profile]["duration_seconds"]
            done, _pending = await asyncio.wait(
                [*tasks, stop_task], timeout=duration, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not stop_task and not task.cancelled() and task.exception():
                    self.error = type(task.exception()).__name__
                    self.reason = "capture_failure"
            self.stop.set()
            for task in [*tasks, stop_task]:
                task.cancel()
            await asyncio.gather(*tasks, stop_task, return_exceptions=True)
        self.writer.close()
        self.event("capture_ended", reason=self.reason, error_type=self.error)
        self.events.close()
        atomic_save(self.output / "status.json", self.status(terminal=True))
        save(
            self.output / "receipts.json",
            {
                str(path.relative_to(self.output)): sha(path)
                for path in sorted(self.output.rglob("*"))
                if path.is_file()
            },
        )
        print(self.status(terminal=True), flush=True)
        if self.error:
            raise RuntimeError("Capture failed; partial evidence preserved")


def capture(plan_path, output, profile):
    if output.exists():
        raise ValueError("Use a new capture directory")
    plan = read(plan_path)
    if (
        {plan["sources"][key] for key in ["depth", "trades", "clock"]}
        != {
            "wss://fstream.binance.com/public/stream",
            "wss://fstream.binance.com/market/stream",
            "https://fapi.binance.com/fapi/v1/time",
        }
        or plan["orders_enabled"]
    ):
        raise ValueError("Only registered public market endpoints allowed")
    if profile not in plan["profiles"]:
        raise ValueError("Unknown registered profile")
    output.mkdir(parents=True)
    if shutil.disk_usage(output).free < plan["capture"]["minimum_free_disk_bytes"]:
        raise OSError("Insufficient registered capture disk headroom")
    save(output / "plan.json", plan)
    source = Path(__file__)
    (output / "capture_source.py").write_bytes(source.read_bytes())
    save(
        output / "input_freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "capture_source_sha256": sha(source),
            "python_pid": os.getpid(),
            "aiohttp_version": aiohttp.__version__,
            "profile": profile,
            "started_utc": datetime.now(UTC).isoformat(),
            "orders_enabled": False,
        },
    )
    asyncio.run(Capture(plan, output, profile).run())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=["pilot", "main"], required=True)
    args = parser.parse_args()
    capture(args.plan.absolute(), args.output.absolute(), args.profile)
