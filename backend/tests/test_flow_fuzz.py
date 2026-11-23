"""小规模网络下，把最大流边界与穷举全部可行匹配对照。"""
import itertools
import random
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
stub = types.ModuleType('src.utils.crypto')
stub.signed = lambda row: True
sys.modules.setdefault('src.utils.crypto', stub)

from src.services.lineage import _Edge, _FamilyFlow  # noqa: E402


def brute(nodes, quantities, edges):
    feasible = []
    best = -1
    for values in itertools.product(*[range(edge.capacity + 1) for edge in edges]):
        out = {n: 0 for n in nodes}
        inflow = {n: 0 for n in nodes}
        for edge, value in zip(edges, values):
            out[edge.u] += value
            inflow[edge.v] += value
        if any(out[n] > quantities[n] or inflow[n] > quantities[n] for n in nodes):
            continue
        total = sum(values)
        best = max(best, total)
        feasible.append((total, values, inflow))
    maximum = [item for item in feasible if item[0] == best]
    mins = [min(values[i] for _, values, _ in maximum) for i in range(len(edges))]
    maxs = [max(values[i] for _, values, _ in maximum) for i in range(len(edges))]
    inflow_max = {n: max(item[2][n] for item in maximum) for n in nodes}
    return best, mins, maxs, inflow_max


def main():
    random.seed(11)
    for _ in range(120):
        count = random.randint(2, 3)
        nodes = list(range(count))
        quantities = {n: random.randint(1, 3) for n in nodes}
        edges = []
        for u in nodes:
            for v in nodes:
                if u != v and random.random() < 0.5:
                    edges.append(_Edge(u, v, random.randint(1, 3)))
        flow = _FamilyFlow(nodes, edges, quantities)
        best, mins, maxs, inflow_max = brute(nodes, quantities, edges)
        if flow.baseline() != best:
            raise AssertionError(('baseline', nodes, quantities, edges, best, flow.baseline()))
        for i in range(len(edges)):
            if flow.edge_min(i) != mins[i] or flow.edge_max(i) != maxs[i]:
                raise AssertionError(('edge', i, edges, mins[i], maxs[i],
                                      flow.edge_min(i), flow.edge_max(i)))
        for node in nodes:
            if flow.inflow_max(node) != inflow_max[node]:
                raise AssertionError(('inflow', node, edges, quantities,
                                      inflow_max[node], flow.inflow_max(node)))
    print('flow fuzz: 120 cases all match brute force')


if __name__ == '__main__':
    main()
