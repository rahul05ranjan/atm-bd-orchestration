"""Select a phase's current stack top from its canonical bead ledger."""


def stack_bead_id(root: str) -> str:
    """Return the stack ledger bead ID for a phase root."""
    return f"{root}.stack"


def pending_handoffs(bead):
    """Read pending post-PASS stack handoffs from the stack ledger metadata."""
    metadata = bead.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("stack bead metadata must be an object or null")
    pending = (metadata or {}).get("pending_handoffs", [])
    if not isinstance(pending, list) or any(not isinstance(item, dict) for item in pending):
        raise ValueError("stack bead metadata.pending_handoffs must be an array of objects")
    return pending


def select_stack_top(bead, pr_target, resolve_head):
    metadata = bead.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("stack bead metadata must be an object or null")
    layers = (metadata or {}).get("layers", [])
    pending = pending_handoffs(bead)
    if pending:
        refs = ", ".join(str(item.get("checked_bead", "?")) for item in pending)
        raise ValueError(f"stack has pending sanity handoff(s): {refs}")
    if not isinstance(layers, list):
        raise ValueError("stack bead metadata.layers must be an array")
    if layers:
        layer = layers[-1]
        if not isinstance(layer, dict) or any(
            not isinstance(layer.get(name), str) or not layer[name].strip()
            for name in ("branch", "head")
        ):
            raise ValueError("last stack layer must have non-empty branch and head strings")
        return layer["branch"], layer["head"]
    if not isinstance(pr_target, str) or not pr_target.strip():
        raise ValueError("pr_target must be a non-empty string")
    head = resolve_head(pr_target)
    if not isinstance(head, str) or not head.strip():
        raise ValueError(f"origin/{pr_target} has no resolvable head")
    return pr_target, head
