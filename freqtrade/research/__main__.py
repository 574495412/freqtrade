"""python -m freqtrade.research：独立、有限、原生引擎的加密研发入口。"""

import argparse
import json
from pathlib import Path

from .data import prepare_data, read
from .workflow import evaluate, review, train, validate_plan


def main():
    parser = argparse.ArgumentParser(description="加密量化研发；默认不操作真实账户")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "train", "review", "evaluate", "bias"):
        command = sub.add_parser(name)
        command.add_argument("--dataset", type=Path, required=True)
        if name in ("prepare", "train"):
            command.add_argument("--plan", type=Path, default=Path("research/crypto/plan.json"))
        if name == "prepare":
            command.add_argument("--source", type=Path, required=True)
            command.add_argument("--resume", action="store_true")
        else:
            command.add_argument("--run", type=Path, required=True)
        if name == "review":
            command.add_argument("--tradingagents-root", type=Path, required=True)
            command.add_argument("--tradingagents-python", type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    if args.command in ("prepare", "train"):
        plan = read(args.plan)
        validate_plan(plan)
    if args.command == "prepare":
        result = prepare_data(args.source.resolve(), dataset, plan, resume=args.resume)
    elif args.command == "train":
        result = train(plan, dataset, args.run.resolve())
    elif args.command == "review":
        result = review(
            args.run.resolve(),
            dataset,
            args.tradingagents_root.resolve(),
            args.tradingagents_python.absolute(),
        )
    elif args.command == "evaluate":
        result = evaluate(args.run.resolve(), dataset)
    else:
        from .validation import bias

        result = bias(args.run.resolve(), dataset)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
