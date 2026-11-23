"""转借来源溯源。

口径（与业务约定一致）：
- 以运行快照为准：截至方案末日生效的冲销，其原签领先整体排除（即使冲销在窗口前补录）。
- 仅在窗口内、同器材族的未冲销签领之间接续：上一张的入借馆等于下一张的出借馆，
  且生效日严格更早；同日签领不猜先后。
- 一张签领可拆给多个去向，每件在前后各最多接一次，求全窗接续件次最大的匹配（最大流）。
- 接不上的件次从该单出借馆起算；若并列最优接法会让某件的来源馆不同，则计为来源不明。
"""
from collections import defaultdict
from dataclasses import dataclass

from src.constants.domain import ReceiptKind
from src.utils.crypto import signed


class LineageMismatch(ValueError):
    pass


@dataclass
class _Edge:
    """同一族内、可接续的两张签领 u -> v（u 的入借馆是 v 的出借馆）。"""
    u: int
    v: int
    capacity: int


class _Dinic:
    def __init__(self, size):
        self.size = size
        self.graph = [[] for _ in range(size)]
        self.level = [-1] * size
        self.ptr = [0] * size

    def add_edge(self, fr, to, capacity):
        forward = [to, capacity, len(self.graph[to])]
        backward = [fr, 0, len(self.graph[fr])]
        self.graph[fr].append(forward)
        self.graph[to].append(backward)
        return len(self.graph[fr]) - 1

    def _bfs(self, source, sink):
        self.level = [-1] * self.size
        self.level[source] = 0
        queue = [source]
        head = 0
        while head < len(queue):
            node = queue[head]
            head += 1
            for to, capacity, _ in self.graph[node]:
                if capacity > 0 and self.level[to] < 0:
                    self.level[to] = self.level[node] + 1
                    queue.append(to)
        return self.level[sink] >= 0

    def _dfs(self, node, sink, pushed):
        if node == sink or not pushed:
            return pushed
        total = 0
        while self.ptr[node] < len(self.graph[node]):
            to, capacity, reverse_index = self.graph[node][self.ptr[node]]
            if capacity > 0 and self.level[to] == self.level[node] + 1:
                flow = self._dfs(to, sink, min(pushed - total, capacity))
                if flow:
                    self.graph[node][self.ptr[node]][1] -= flow
                    self.graph[to][reverse_index][1] += flow
                    total += flow
                    if total == pushed:
                        return total
            self.ptr[node] += 1
        return total

    def max_flow(self, source, sink):
        total = 0
        inf = 10 ** 12
        while self._bfs(source, sink):
            self.ptr = [0] * self.size
            while True:
                pushed = self._dfs(source, sink, inf)
                if not pushed:
                    break
                total += pushed
        return total


class _FamilyFlow:
    """一族器材在窗口内的接续网络（件数展开的二分匹配，边容量即可接续件数）。"""

    def __init__(self, nodes, edges, quantities):
        self.nodes = nodes
        self.node_index = {node_id: index for index, node_id in enumerate(nodes)}
        self.edges = edges
        self.quantities = quantities
        self._baseline_value = None

    def _plain_network(self, drops=frozenset()):
        size = 2 * len(self.nodes) + 2
        source = size - 2
        sink = size - 1
        net = _Dinic(size)
        for index, node_id in enumerate(self.nodes):
            net.add_edge(source, index, self.quantities[node_id])
            net.add_edge(len(self.nodes) + index, sink, self.quantities[node_id])
        for edge_index, edge in enumerate(self.edges):
            if edge_index in drops:
                continue
            net.add_edge(self.node_index[edge.u], len(self.nodes) + self.node_index[edge.v],
                         edge.capacity)
        return net, source, sink

    def baseline(self):
        if self._baseline_value is None:
            net, source, sink = self._plain_network()
            self._baseline_value = net.max_flow(source, sink)
        return self._baseline_value

    def edge_min(self, edge_index):
        """该边在所有最大匹配中至少承担的件次 = 移除该边后损失的接续量。"""
        net, source, sink = self._plain_network(drops=frozenset({edge_index}))
        return self.baseline() - net.max_flow(source, sink)

    def edge_max(self, edge_index):
        """该边在某个最大匹配中最多承担的件次（对下界 k 做可行流二分）。"""
        lo, hi = 0, self.edges[edge_index].capacity
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._edge_feasible(edge_index, mid):
                lo = mid
            else:
                hi = mid - 1
        return lo

    def inflow_max(self, node_id):
        """该签领在某个最大匹配中最多接续到的件次（入边合计上限，对下界 k 二分）。"""
        lo, hi = 0, self.quantities[node_id]
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._inflow_feasible(node_id, mid):
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _lower_bound_flow(self, edge_lower=(), sink_lower=()):
        """对内部弧 (u,v) 与汇点弧 vR→sink 施加下界，返回满足下界时能达到的最大总流量；
        不可行返回 -1。标准下界可行环流（超级源/汇补平衡 + sink→source 回流边），
        环流成立后再沿残余网络从原源到原汇增广到最大。"""
        count = len(self.nodes)
        size = 2 * count + 4
        source, sink = 2 * count, 2 * count + 1
        super_source, super_sink = 2 * count + 2, 2 * count + 3
        net = _Dinic(size)
        for index, node_id in enumerate(self.nodes):
            net.add_edge(source, index, self.quantities[node_id])
            net.add_edge(count + index, sink, self.quantities[node_id])
        net.add_edge(sink, source, 10 ** 9)
        balance = [0] * size
        lowered_keys = {(left, right): lower for left, right, lower in edge_lower}
        sink_lower_map = dict(sink_lower)

        for left, right, lower in edge_lower:
            li = self.node_index[left]
            ri = count + self.node_index[right]
            net.add_edge(li, ri, self._edge_capacity(left, right) - lower)
            balance[li] -= lower
            balance[ri] += lower
        for edge in self.edges:
            if (edge.u, edge.v) in lowered_keys:
                continue
            net.add_edge(self.node_index[edge.u], count + self.node_index[edge.v], edge.capacity)
        for node_id, lower in sink_lower_map.items():
            ri = count + self.node_index[node_id]
            arc_index = next(i for i, arc in enumerate(net.graph[ri]) if arc[0] == sink)
            net.graph[ri][arc_index][1] -= lower
            balance[ri] -= lower
            balance[sink] += lower

        total_demand = 0
        for index, value in enumerate(balance):
            if value > 0:
                net.add_edge(super_source, index, value)
                total_demand += value
            elif value < 0:
                net.add_edge(index, super_sink, -value)
        if total_demand and net.max_flow(super_source, super_sink) != total_demand:
            return -1
        return net.max_flow(source, sink)

    def _edge_capacity(self, left, right):
        for edge in self.edges:
            if edge.u == left and edge.v == right:
                return edge.capacity
        return 0

    def _edge_feasible(self, edge_index, lower):
        if lower == 0:
            return True
        edge = self.edges[edge_index]
        return self._lower_bound_flow(edge_lower=((edge.u, edge.v, lower),)) == self.baseline()

    def _inflow_feasible(self, node_id, lower):
        if lower == 0:
            return True
        # vR→sink 下界 k：强制 v 的入边合计至少 k 件，且不放宽其节点容量。
        return self._lower_bound_flow(sink_lower=((node_id, lower),)) == self.baseline()


def _trace_family(loans, lookup):
    loans = sorted(loans, key=lambda row: (row['effective_on'], row['id']))
    node_ids = [row['id'] for row in loans]
    quantities = {row['id']: row['quantity'] for row in loans}
    edges = []
    for later in loans:
        for earlier in loans:
            if (earlier['destination'] == later['source']
                    and earlier['effective_on'] < later['effective_on']):
                edges.append(_Edge(earlier['id'], later['id'],
                                   min(quantities[earlier['id']], quantities[later['id']])))
    flow = _FamilyFlow(node_ids, edges, quantities)
    maximum = flow.baseline()

    feasible = []
    for edge_index, edge in enumerate(edges):
        upper = flow.edge_max(edge_index)
        if upper == 0:
            continue
        lower = flow.edge_min(edge_index)
        feasible.append({
            'edge_index': edge_index,
            'up': lookup[edge.u]['reference'],
            'down': lookup[edge.v]['reference'],
            'min': lower,
            'max': upper,
            'fixed': lower == upper,
        })

    inflow_limit = {node_id: flow.inflow_max(node_id) for node_id in node_ids}
    incoming = defaultdict(list)
    for item in feasible:
        incoming[edges[item['edge_index']].v].append(item)

    # 按生效日拓扑（DAG）逐单传递：每件器材在所有最大匹配中固定的来源馆与来源不明件数。
    possible_origins = {}
    guaranteed = {}
    unknown_count = {}
    receipts = []
    unknown_total = 0
    for row in loans:
        node_id = row['id']
        quantity = quantities[node_id]
        origins = {row['source']}
        for item in incoming[node_id]:
            origins.update(possible_origins[edges[item['edge_index']].u])
        possible_origins[node_id] = origins

        # 接续不上的件次在任何最大匹配中都留存在本单，固定从出借馆起算。
        own_guaranteed = quantity - inflow_limit[node_id]
        fixed = {row['source']: own_guaranteed} if own_guaranteed else {}
        uncertain = 0
        for item in incoming[node_id]:
            edge = edges[item['edge_index']]
            upstream = edge.u
            f_min = item['min']
            upstream_quantity = quantities[upstream]
            # 该边至少 f_min 件；上游 q 件中只有 (q-g) 件不是某馆来源，抽屉原理给出下界。
            for hall, g in guaranteed[upstream].items():
                bound = max(0, f_min - (upstream_quantity - g))
                if bound:
                    fixed[hall] = fixed.get(hall, 0) + bound
            # 上游本身来源不明的件，经固定流量带到本单的下界。
            uncertain += max(0, f_min - (upstream_quantity - unknown_count[upstream]))
        # 边流量在 min/max 间浮动、未被下界覆盖的件，来源馆会随并列接法改变。
        uncertain += quantity - own_guaranteed - sum(item['min'] for item in incoming[node_id])
        guaranteed[node_id] = fixed
        unknown_count[node_id] = uncertain
        unknown_total += uncertain
        receipts.append({
            'reference': row['reference'],
            'effective_on': str(row['effective_on']),
            'source': row['source'],
            'destination': row['destination'],
            'quantity': quantity,
            'origins': sorted(origins),
            'guaranteed_origins': [{'hall': hall, 'quantity': value}
                                   for hall, value in sorted(fixed.items()) if value],
            'unknown_quantity': uncertain,
            'incoming': [{'reference': item['up'], 'min': item['min'], 'max': item['max'],
                          'fixed': item['fixed']} for item in incoming[node_id]],
        })

    links = [{'up': item['up'], 'down': item['down'], 'min': item['min'],
              'max': item['max'], 'fixed': item['fixed']} for item in feasible]
    return {
        'active_quantity': sum(quantities.values()),
        'chained_quantity': maximum,
        'unknown_quantity': unknown_total,
        'receipts': receipts,
        'links': links,
    }


def trace_lineage(snapshot):
    receipts = snapshot['receipts']
    # 窗口前补录、但在末日前生效的冲销同样排除其原签领；只影响基于该快照的新报告。
    prior_reversals = snapshot.get('prior_reversals', [])
    for row in prior_reversals:
        if row['kind'] != ReceiptKind.REVERSAL or not signed(row):
            raise LineageMismatch(f'{row["reference"]} 的签名校验失败')
    reversed_originals = {row['original_id'] for row in receipts
                          if row['kind'] == ReceiptKind.REVERSAL}
    reversed_originals.update(row['original_id'] for row in prior_reversals)

    families = [item['code'] for item in snapshot['instruments']]
    lookup = {row['id']: row for row in receipts}
    grouped = defaultdict(list)
    excluded = []
    for row in receipts:
        if row['kind'] != ReceiptKind.LOAN:
            continue
        if row['id'] in reversed_originals:
            excluded.append(row['reference'])
            continue
        grouped[row['instrument']].append(row)

    result_families = {}
    total_active = total_chained = total_unknown = 0
    for family in families:
        traced = _trace_family(grouped.get(family, []), lookup)
        result_families[family] = traced
        total_active += traced['active_quantity']
        total_chained += traced['chained_quantity']
        total_unknown += traced['unknown_quantity']
    return {
        'families': result_families,
        'active_quantity': total_active,
        'chained_quantity': total_chained,
        'unknown_quantity': total_unknown,
        'excluded_reversed': sorted(excluded),
        'prior_reversal_refs': sorted(row['reference'] for row in prior_reversals),
    }
