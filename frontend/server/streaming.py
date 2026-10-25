"""Bounded line assembly before redaction, including split UTF-8/secrets."""

from __future__ import annotations


class OutputLines:
    def __init__(self, redact, limit=65536):
        self.redact = redact
        self.limit = limit
        self.pending = b""
        self.discarding = False

    def feed(self, chunk: bytes, final=False) -> str:
        self.pending += chunk
        if self.discarding:
            newline = self.pending.find(b"\n")
            if newline < 0:
                self.pending = b""
                return ""
            self.pending = self.pending[newline + 1 :]
            self.discarding = False
        output = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if len(line) > self.limit:
                output.append("[oversized output line omitted]\n")
            else:
                output.append(self.redact((line + b"\n").decode(errors="replace")))
        if len(self.pending) > self.limit:
            self.pending = b""
            self.discarding = True
            output.append("[oversized output line omitted]\n")
        if final and self.pending:
            output.append(self.redact(self.pending.decode(errors="replace")))
            self.pending = b""
        return "".join(output)
