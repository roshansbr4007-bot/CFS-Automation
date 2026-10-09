import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DateTimeText, formatIST } from "./DateTimeText";

describe("DateTimeText (F5)", () => {
  it("shows a UTC timestamp in IST", () => {
    expect(formatIST("2026-10-05T18:29:00Z")).toMatch(/5 Oct 2026.*11:59/i);
  });

  it("keeps the original value in the time element", () => {
    render(<DateTimeText value="2026-10-05T18:29:00Z" />);
    expect(screen.getByText(/11:59/)).toHaveAttribute("datetime", "2026-10-05T18:29:00Z");
  });

  it("shows a dash for missing values", () => {
    render(<DateTimeText value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
