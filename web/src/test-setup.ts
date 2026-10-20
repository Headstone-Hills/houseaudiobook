import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// jsdom has no canvas; the Waveform component skips drawing without a context.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

// A WebSocket stand-in so components can subscribe without a server. Tests push
// messages with FakeWebSocket.last?.emit(...).
export class FakeWebSocket {
  static last: FakeWebSocket | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.last = this;
  }
  emit(data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
  close() {}
}
globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
