"""Add versioned rule commands alongside the legacy command parser."""

import argparse
import json
import sys
from pathlib import Path

from pipeforge.config import read_yaml
from pipeforge.errors import ConfigError
from pipeforge.logging import safe_text
from pipeforge.rule_ci import export, import_native, inspect_wrapper
from pipeforge.rule_runtime import RuleRuntime, nodes, write_events
from pipeforge.rules import LEGACY, RULE_COMMANDS, discover, discover_file, get_rule, load_rules


def normalize(argv: list[str]) -> list[str]:
    if len(argv) >= 3 and argv[0] in {"-f", "--file"}:
        return [argv[2], *argv[:2], *argv[3:]]
    if len(argv) >= 2 and argv[0].startswith("--file="):
        return [argv[1], argv[0], *argv[2:]]
    return argv


def dispatch(argv: list[str]) -> int | None:
    if not argv or argv[0].startswith("-"):
        return None
    command = argv[0]
    if command in LEGACY and command != "validate":
        return None
    parser = argparse.ArgumentParser(prog=f"pipeforge {command}")
    parser.add_argument("-f", "--file", type=Path)
    if command == "validate":
        parser.add_argument("--color", choices=("auto", "always", "never"))
    elif command == "explain":
        parser.add_argument("rule")
    elif command == "completion":
        parser.add_argument("shell", choices=("bash", "zsh"))
    elif command == "export":
        parser.add_argument("provider", choices=("github", "gitlab", "jenkins"))
        parser.add_argument("rule")
        parser.add_argument("--mode", choices=("wrapper", "standalone"), default="wrapper")
        parser.add_argument("-o", "--output", type=Path)
    elif command == "import":
        parser.add_argument("provider", choices=("github", "gitlab", "jenkins"))
        parser.add_argument("workflow", type=Path)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--standalone-rule", metavar="RULE")
        mode.add_argument("--native", action="store_true")
    elif command == "mcp":
        parser.add_argument("events", type=Path)
    elif command == "observe":
        parser.add_argument("snapshot", type=Path)
        parser.add_argument("--desired-digest", required=True)
        parser.add_argument("--observed-digest")
    elif command == "promote":
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--container", required=True)
        parser.add_argument("--digest", required=True)
        parser.add_argument("--observed-digest")
        parser.add_argument("-o", "--output", type=Path)
    elif command == "studio":
        parser.add_argument("events", type=Path)
        parser.add_argument("-o", "--output", type=Path, required=True)
        parser.add_argument("--format", choices=("html", "svg", "json"), default="html")
    elif command not in RULE_COMMANDS:
        parser.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--events", type=Path)

    args = parser.parse_args(argv[1:])
    if command == "import" and args.native:
        print(json.dumps(import_native(args.provider, args.workflow), indent=2))
        return 0
    if command == "mcp":
        from pipeforge.rule_mcp import serve

        try:
            serve(args.events)
        except ConfigError as error:
            print(safe_text(str(error)), file=sys.stderr)
            return 2
        except OSError:
            print("Cannot read the journal or use MCP stdio.", file=sys.stderr)
            return 1
        return 0
    if command == "observe":
        from pipeforge.gitops import observe

        print(json.dumps(observe(args.snapshot, args.desired_digest, args.observed_digest)))
        return 0
    if command == "promote":
        from pipeforge.gitops import draft

        promotion = draft(args.manifest, args.container, args.digest, args.observed_digest)
        status = "unknown" if promotion.drift is None else "yes" if promotion.drift else "no"
        print(f"Desired {promotion.desired}. Drift {status}.", file=sys.stderr)
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(promotion.patch)
        else:
            print(promotion.patch, end="")
        return 0
    if command == "studio":
        from pipeforge.studio import report

        report(args.events, args.output, args.format)
        return 0
    if command == "validate":
        args.file = args.file or discover_file()
        if args.file is None:
            return None
        data = read_yaml(args.file)
        if not isinstance(data, dict) or "version" not in data:
            return None
    file = args.file or discover()
    spec = load_rules(file)
    if command == "validate":
        print("Rule configuration valid.")
    elif command == "list":
        print("\n".join(sorted(spec.rules)))
    elif command == "explain":
        rule = get_rule(spec.rules, args.rule)
        print(safe_text(f"{rule.name}  {rule.source.file}:{rule.source.line}"))
        for name, field in rule.inputs.items():
            print(
                f"  {name}: {field.kind} ({'required' if field.default is None else 'defaulted'})"
            )
        for group in rule.steps:
            print("  " + " + ".join(call.rule for call in group))
    elif command == "completion":
        choices = " ".join(sorted(LEGACY | RULE_COMMANDS | spec.rules.keys()))
        if args.shell == "bash":
            print(f"complete -W '{choices}' pipeforge")
        else:
            print(f"_pipeforge() {{ compadd -- {choices}; }}\ncompdef _pipeforge pipeforge")
    elif command == "import":
        print(
            json.dumps(
                inspect_wrapper(spec, file, args.provider, args.workflow, args.standalone_rule)
            )
        )
    elif command == "export":
        content = export(spec, file, args.rule, args.provider, args.mode)
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(content)
        else:
            print(content, end="")
    else:
        rule = get_rule(spec.rules, command)
        inputs = {}
        for entry in args.input:
            key, separator, value = entry.partition("=")
            if not separator or key not in rule.inputs or key in inputs:
                raise ConfigError("Expected one --input KEY=VALUE per declared input.")
            if rule.inputs[key].kind != "string":
                try:
                    value = json.loads(value)
                except ValueError:
                    raise ConfigError(f"Input {key} requires a JSON integer or boolean.") from None
            inputs[key] = value
        root = spec.graph(command, inputs)
        if args.dry_run:
            for node in nodes(root):
                print(f"{node.id} {node.rule.name} needs {', '.join(node.dependencies) or '-'}")
            return 0
        if args.events:
            with write_events(args.events) as journal:
                return RuleRuntime(spec, sys.stdout, journal).invoke(root)
        return RuleRuntime(spec, sys.stdout, None).invoke(root)
    return 0
