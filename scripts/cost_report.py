#!/usr/bin/env python3
"""Cost report from Cost Explorer, including the provider-specific Bedrock lines.

The gateway's spend log answers "what did the models cost this deployment"; this report answers the question the
client's finance person asks: what is the AWS account spending, and which provider lines make it up. The provider
lines for Claude and Cohere arrive as their own Cost Explorer service names, separate from the generic Amazon
Bedrock line. Reading only "Amazon Bedrock" would understate the bill and miss the provider line entirely.

Run on the build box with the instance role, or anywhere with ce:GetCostAndUsage. The report is intentionally
read-only and prints a stable JSON shape plus a short markdown table.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import boto3

SERVICES = ["Claude", "Cohere", "Bedrock Edition", "Marketplace", "Amazon Bedrock"]


def month_periods(today: date) -> list[tuple[date, date, str]]:
    current_start = today.replace(day=1)
    previous_start = (current_start - timedelta(days=1)).replace(day=1)
    return [
        (previous_start, current_start, "previous month"),
        (current_start, today + timedelta(days=1), "current month to date"),
    ]


def costs_for(client, start: date, end: date) -> dict:
    response = client.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(end)},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    services = {}
    total = 0.0
    for result in response.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            amount = round(float(group["Metrics"]["UnblendedCost"]["Amount"]), 4)
            if abs(amount) < 0.00005:
                continue
            services[group["Keys"][0]] = services.get(group["Keys"][0], 0.0) + amount
            total += amount
    provider_lines = {name: amount for name, amount in services.items()
                      if any(marker.lower() in name.lower() for marker in SERVICES)}
    return {
        "total": round(total, 4),
        "services": dict(sorted(services.items(), key=lambda item: -abs(item[1]))),
        "provider_lines": dict(sorted(provider_lines.items(), key=lambda item: -abs(item[1]))),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AWS cost report for the deployment")
    parser.add_argument("--out", help="write the JSON report here")
    parser.add_argument("--account", help="expected account id; fails on a different one")
    args = parser.parse_args(argv)
    client = boto3.client("ce", region_name="us-east-1")
    identity = boto3.client("sts", region_name="us-east-1").get_caller_identity()
    account_id = identity.get("Account", "")
    if args.account and account_id != args.account:
        print(f"refusing: this session is account {account_id}, not {args.account}")
        return 2
    report = {"account": account_id, "generated_at": date.today().isoformat(), "periods": []}
    for start, end, label in month_periods(date.today()):
        report["periods"].append({"label": label, "start": str(start), "end": str(end),
                                   **costs_for(client, start, end)})
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
    print(text)
    for period in report["periods"]:
        print(f"\n{period['label']} ({period['start']} to {period['end']}): {period['total']:.2f} USD")
        for name, amount in list(period["provider_lines"].items()):
            print(f"  provider {amount:>10.2f}  {name}")
        for name, amount in list(period["services"].items())[:10]:
            print(f"  service  {amount:>10.2f}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
