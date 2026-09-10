import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


def send(base_url: str, number: int) -> tuple[bool, float]:
    payload = json.dumps(
        {
            "channel": "whatsapp",
            "external_user_id": f"platform-load-{number}",
            "text": "What is DUDU Car?",
            "user_role": "rider",
        }
    ).encode()
    started = time.monotonic()
    try:
        with urlopen(
            Request(
                f"{base_url.rstrip('/')}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            ),
            timeout=35,
        ) as response:
            return response.status == 200, time.monotonic() - started
    except Exception:
        return False, time.monotonic() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.requests <= 15_000 or not 1 <= args.concurrency <= 20:
        raise SystemExit("requests must be 1..15000 and concurrency 1..20")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            as_completed(pool.submit(send, args.base_url, number) for number in range(args.requests))
        )
        completed = [future.result() for future in results]
    latencies = sorted(latency for _, latency in completed)
    successes = sum(ok for ok, _ in completed)
    report = {
        "requests": args.requests,
        "successes": successes,
        "success_rate": successes / args.requests,
        "p95_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1],
        "elapsed_seconds": time.monotonic() - started,
    }
    print(json.dumps(report, sort_keys=True))
    if report["success_rate"] < 0.95 or report["p95_seconds"] > 30:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
