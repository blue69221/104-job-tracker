# -*- coding: utf-8 -*-
"""職類 / 地區代碼樹的展開與查詢。"""


def index_tree(nodes, parent=None, out=None):
    """把巢狀的 104 代碼樹壓平成 {code: {des, children, parent}}。"""
    if out is None:
        out = {}
    for node in nodes or []:
        code = node["no"]
        children = [c["no"] for c in (node.get("n") or [])]
        out[code] = {"des": node["des"], "children": children, "parent": parent}
        index_tree(node.get("n"), code, out)
    return out


def leaves_under(index, code):
    """回傳 code 底下所有葉節點；code 本身是葉節點時回傳它自己。"""
    node = index.get(code)
    if node is None:
        raise KeyError(f"未知代碼: {code}（104 可能已調整分類）")
    if not node["children"]:
        return [code]
    out = []
    for child in node["children"]:
        out.extend(leaves_under(index, child))
    return out


def path_of(index, code):
    """組出人類看得懂的階層路徑，例如「品保／品管類人員 > 硬體測試工程師」。"""
    parts, cur = [], code
    while cur and cur in index:
        parts.append(index[cur]["des"])
        cur = index[cur]["parent"]
    return " > ".join(reversed(parts))
