#!/usr/bin/env python3
"""
wiring.py

Prints the Black-Mata electrical wiring as a tree and connection table
in the terminal. Reads from nodes.csv and edges.csv in the same directory.
If servo_map.json exists, servo labels are enriched with real IDs.

Usage:
    python3 docs/wiring/wiring.py
"""

import csv
import io
import json
import os
import pydoc
import sys
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))


def load_csv(filename):
    with open(os.path.join(DIR, filename), newline="") as f:
        return list(csv.DictReader(f))


def load_servo_map():
    path = os.path.join(DIR, "servo_map.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def apply_servo_map(nodes, servo_map):
    """Enrich servo node labels and info with real IDs from servo_map."""
    id_by_label = {v["label"]: k for k, v in servo_map.items()}
    for node in nodes:
        label = node["label"]
        # Match by label prefix e.g. "FL Steering AX-12A" matches "FL_steering"
        for servo_label, servo_id in id_by_label.items():
            normalized = servo_label.replace("_", " ").lower()
            if normalized in label.lower():
                node["info"] = "ID: {} · {}".format(servo_id, node["info"])
                break
    return nodes


def edge_annotation(edge):
    parts = [edge["voltage"], edge["current"], edge["protocol"]]
    return "  ".join(p for p in parts if p)


def print_tree(nodes, edges):
    node_map = {n["id"]: n for n in nodes}
    children = defaultdict(list)
    has_parent = set()

    for e in edges:
        children[e["from"]].append(e)
        has_parent.add(e["to"])

    roots = [n["id"] for n in nodes if n["id"] not in has_parent]

    def render(node_id, prefix="", last=True):
        node = node_map.get(node_id, {"label": node_id, "info": ""})
        connector = "└─► " if last else "├─► "
        label = node["label"]
        info = "  ({})".format(node["info"]) if node["info"] else ""
        print("{}{}{}{}".format(prefix, connector, label, info))
        child_edges = children.get(node_id, [])
        new_prefix = prefix + ("    " if last else "│   ")
        for i, edge in enumerate(child_edges):
            is_last = (i == len(child_edges) - 1)
            ann = edge_annotation(edge)
            if ann:
                wire_connector = "└── " if is_last else "├── "
                print("{}{}[{}]".format(new_prefix, wire_connector, ann))
            render(edge["to"], new_prefix, is_last)

    print("\n=== Wiring Topology ===\n")
    for i, root in enumerate(roots):
        render(root, last=(i == len(roots) - 1))


def print_table(nodes, edges):
    node_map = {n["id"]: n for n in nodes}

    col_from     = max(len("From"),     max(len(node_map.get(e["from"], {}).get("label", e["from"])) for e in edges))
    col_to       = max(len("To"),       max(len(node_map.get(e["to"],   {}).get("label", e["to"]))   for e in edges))
    col_voltage  = max(len("Voltage"),  max(len(e["voltage"])  for e in edges))
    col_current  = max(len("Current"),  max(len(e["current"])  for e in edges))
    col_protocol = max(len("Protocol"), max(len(e["protocol"]) for e in edges))

    fmt = "  {{:<{}}}  {{:<{}}}  {{:<{}}}  {{:<{}}}  {{:<{}}}".format(
        col_from, col_to, col_voltage, col_current, col_protocol)
    sep = "  {}  {}  {}  {}  {}".format(
        "-" * col_from, "-" * col_to, "-" * col_voltage,
        "-" * col_current, "-" * col_protocol)

    print("\n=== Connection Table ===\n")
    print(fmt.format("From", "To", "Voltage", "Current", "Protocol"))
    print(sep)
    for e in edges:
        from_label = node_map.get(e["from"], {}).get("label", e["from"])
        to_label   = node_map.get(e["to"],   {}).get("label", e["to"])
        print(fmt.format(from_label, to_label, e["voltage"], e["current"], e["protocol"]))


def print_component_table(nodes):
    col_label = max(len("Component"), max(len(n["label"]) for n in nodes))
    col_group = max(len("Group"),     max(len(n["group"]) for n in nodes))
    col_info  = max(len("Info"),      max(len(n["info"])  for n in nodes))

    fmt = "  {{:<{}}}  {{:<{}}}  {{:<{}}}".format(col_label, col_group, col_info)
    sep = "  {}  {}  {}".format("-" * col_label, "-" * col_group, "-" * col_info)

    print("\n=== Component Info ===\n")
    print(fmt.format("Component", "Group", "Info"))
    print(sep)
    for n in nodes:
        print(fmt.format(n["label"], n["group"], n["info"]))


def main():
    nodes = load_csv("nodes.csv")
    edges = load_csv("edges.csv")
    servo_map = load_servo_map()

    if servo_map:
        nodes = apply_servo_map(nodes, servo_map)

    # Capture output then page it if running in a terminal
    buf = io.StringIO()
    sys_stdout = sys.stdout
    sys.stdout = buf

    if servo_map:
        print("(servo_map.json loaded — servo IDs applied)\n")
    print_tree(nodes, edges)
    print_table(nodes, edges)
    print_component_table(nodes)

    sys.stdout = sys_stdout
    output = buf.getvalue()

    if sys.stdout.isatty():
        pydoc.pager(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
