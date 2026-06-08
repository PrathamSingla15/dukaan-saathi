"""Confirm-before-write staging store for Dukaan Saathi (R1).

Write tools never touch SQLite directly. Instead they *stage* a pending op into
an in-process batch keyed by ``thread_id``; the agent then shows the owner a
Hindi preview and waits for a "haan / confirm". Only on commit do we dispatch
each staged op to the real ``dukaan.ops`` write functions (the sole DB writers).

Design decisions:
- No DB schema and no persistence: pending writes live purely in module memory
  (:data:`_PENDING`) and disappear on restart — a dropped batch is just a
  re-ask, never a half-written row.
- Batch ids are DETERMINISTIC (a module-level counter, ``batch-1``, ``batch-2``,
  …) so tests/logs are stable and don't depend on a clock or RNG.
- A process-global ``_ACTIVE_THREAD`` (NOT a contextvar) carries the active
  thread so write tools find their batch: ``run_agent`` binds it right before
  invoke, and a plain global stays visible inside deepagents' copied
  tool-execution context where a ``ContextVar`` would not (a GPU e2e showed the
  ContextVar binding being invisible to tools). Assumes one active turn per
  process at a time (true for the single-shopkeeper app + Gradio's per-session
  queue); a future multi-user Space should read thread_id from the tool's
  injected ``RunnableConfig`` instead.
- Import-light: only :mod:`dukaan.ops` is imported, which loads no model and
  hits no network at import time.
"""

from __future__ import annotations

from dataclasses import dataclass

from dukaan import ops

# --------------------------------------------------------------- staged op model


@dataclass
class StagedOp:
    """One pending write. ``kind`` selects the ``ops.<kind>`` writer; ``args`` are
    splatted into it at commit; ``preview_hi`` is the Hindi line shown for confirm."""

    kind: str  # one of _DISPATCH keys (add_inventory/record_sale/…)
    args: dict
    preview_hi: str


# --------------------------------------------------------------- module state

# thread_id -> {"batch_id": str, "ops": [StagedOp, …]}. Process-memory only.
_PENDING: dict[str, dict] = {}

# Monotonic source of deterministic batch ids (no clock / RNG).
_BATCH_COUNTER: int = 0

# Tools discover their thread here; run_agent binds it (process-global) right
# before invoke. See the module docstring for why this is a plain global, not a
# ContextVar (deepagents' copied tool context hides ContextVar binds).
_ACTIVE_THREAD: str = "default"

# kind -> real writer. The ONLY place staged ops reach the database.
_DISPATCH = {
    "add_inventory": ops.add_inventory,
    "record_sale": ops.record_sale,
    "record_purchase": ops.record_purchase,
    "add_udhaar": ops.add_udhaar,
    "record_payment": ops.record_payment,
}


# --------------------------------------------------------------- thread binding


def bind_thread(tid: str) -> str:
    """Bind the active thread (process-global) to ``tid``; return the previous value."""
    global _ACTIVE_THREAD
    prev, _ACTIVE_THREAD = _ACTIVE_THREAD, (tid or "default")
    return prev


def current_thread() -> str:
    """The active thread bound by the last ``bind_thread`` (``"default"`` if none)."""
    return _ACTIVE_THREAD


# --------------------------------------------------------------- snapshot helper


def _next_batch_id() -> str:
    global _BATCH_COUNTER
    _BATCH_COUNTER += 1
    return f"batch-{_BATCH_COUNTER}"


def _snapshot(thread_id: str) -> dict | None:
    """Public view of a thread's pending batch (``None`` when empty)."""
    batch = _PENDING.get(thread_id)
    if not batch or not batch["ops"]:
        return None
    ops_view = [{"kind": op.kind, "args": op.args, "preview_hi": op.preview_hi}
                for op in batch["ops"]]
    summary_hi = "\n".join(f"{i + 1}. {op.preview_hi}" for i, op in enumerate(batch["ops"]))
    return {"batch_id": batch["batch_id"], "thread_id": thread_id, "ops": ops_view,
            "summary_hi": summary_hi, "needs_confirm": True}


# --------------------------------------------------------------- public API


def stage_op(thread_id: str, kind: str, args: dict, preview_hi: str) -> dict:
    """Append a pending write to ``thread_id``'s batch; return the snapshot.

    A new batch (with a fresh deterministic id) is created on the first op; later
    ops join the same batch so one confirm can commit several writes together.
    """
    batch = _PENDING.get(thread_id)
    if batch is None:
        batch = {"batch_id": _next_batch_id(), "ops": []}
        _PENDING[thread_id] = batch
    batch["ops"].append(StagedOp(kind=kind, args=dict(args), preview_hi=preview_hi))
    return _snapshot(thread_id)


def get_pending(thread_id: str) -> dict | None:
    """Snapshot of ``thread_id``'s pending batch, or ``None`` if nothing staged."""
    return _snapshot(thread_id)


def clear_pending(thread_id: str) -> None:
    """Drop ``thread_id``'s pending batch without writing anything."""
    _PENDING.pop(thread_id, None)


def commit_pending(thread_id: str) -> dict:
    """Dispatch each staged op to its real ``ops.<kind>`` writer, then clear.

    Returns ``{ok, committed, failed, message_hi}``: ``committed`` collects the
    results whose writer returned ``ok`` truthy, ``failed`` the rest. ``message_hi``
    chains the per-op Hindi confirmations. With nothing pending, a no-op result.
    """
    batch = _PENDING.get(thread_id)
    if not batch or not batch["ops"]:
        return {"ok": False, "committed": [], "failed": [],
                "message_hi": "Kuch confirm karne ko nahi tha."}

    committed: list[dict] = []
    failed: list[dict] = []
    for op in batch["ops"]:
        writer = _DISPATCH.get(op.kind)
        if writer is None:
            failed.append({"kind": op.kind, "args": op.args,
                           "message": f"Unknown op '{op.kind}'."})
            continue
        result = writer(**op.args)
        (committed if result.get("ok") else failed).append(result)

    # Clear the batch whether or not every op succeeded — a confirmed batch is
    # consumed; the owner re-states anything that failed rather than re-confirming.
    clear_pending(thread_id)

    parts = [r["message"] for r in committed if r.get("message")]
    if committed and not failed:
        message_hi = " ".join(parts) if parts else "Sab save ho gaya."
    elif committed and failed:
        message_hi = (" ".join(parts) + f" ({len(failed)} cheez save nahi hui.)").strip()
    else:
        message_hi = "Kuch bhi save nahi hua."

    return {"ok": bool(committed) and not failed, "committed": committed,
            "failed": failed, "message_hi": message_hi}
