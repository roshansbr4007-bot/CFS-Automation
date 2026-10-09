import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Countdown, formatRemaining } from "./Countdown";

describe("formatRemaining", () => {
  it.each([
    [3600, "1h 00m remaining"],
    [1800, "30m remaining"],
    [900, "15m remaining"],
    [5400, "1h 30m remaining"],
    [0, "00m remaining"],
    [-120, "00m remaining"],
  ])("%i seconds -> %s", (seconds, text) => {
    expect(formatRemaining(seconds)).toBe(text);
  });
});

describe("Countdown", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-05T06:29:00Z")); });
  afterEach(() => { vi.useRealTimers(); });

  it("counts down from the server's remaining time and shows Overdue at zero", () => {
    render(<Countdown remainingSeconds={60} receivedAt={Date.now()} />);
    expect(screen.getByText("01m remaining")).toBeInTheDocument();
    expect(screen.queryByText("Overdue")).not.toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(61_000); });
    expect(screen.getByText("00m remaining")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });
});
