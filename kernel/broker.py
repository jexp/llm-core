"""Tool broker: the single enforcement point for contract execution (ACE).

Every op execution — from T0 handlers or the interpreter's plan — validates args against the
contract schema before the call and validates the output after; results are audited. Generated
code (M1+) reaches modules only through this broker, with schema-checked inputs and typed
outputs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.audit import AuditLog
    from kernel.contracts import ContractRegistry

Handler = Callable[[dict], dict]


class ExecutionError(Exception):
    def __init__(self, op_id: str, reason: str) -> None:
        super().__init__(f"{op_id}: {reason}")
        self.op_id = op_id
        self.reason = reason


class ToolBroker:
    def __init__(self, contracts: ContractRegistry, audit: AuditLog) -> None:
        self._contracts = contracts
        self._audit = audit
        self._handlers: dict[str, Handler] = {}

    def register(self, op_id: str, handler: Handler) -> None:
        self._handlers[op_id] = handler

    def implemented(self, op_id: str) -> bool:
        return op_id in self._handlers

    def implemented_ops(self) -> list[str]:
        return sorted(self._handlers)

    def execute(
        self,
        op_id: str,
        args: dict,
        request_id: str,
        principal: str,
        dry_run: bool = False,
    ) -> dict:
        op = self._contracts.get(op_id)
        if op is None:
            raise ExecutionError(op_id, "not a contract operation")
        if errors := self._contracts.validate_args(op_id, args):
            raise ExecutionError(op_id, f"invalid args: {'; '.join(errors)}")
        if dry_run:
            return {"dry_run": True, "op": op_id, "args": args}
        handler = self._handlers.get(op_id)
        if handler is None:
            raise ExecutionError(op_id, "no implementation registered (interpreter tier handles this)")
        try:
            output = handler(args)
        except ExecutionError:
            raise
        except Exception as e:
            raise ExecutionError(op_id, f"handler failed: {e}") from e
        if errors := self._contracts.validate_output(op_id, output):
            raise ExecutionError(op_id, f"output violates contract: {'; '.join(errors)}")
        self._audit.append("OP_RESULT", request_id, principal, {"op": op_id, "ok": True, "output": output})
        return output
