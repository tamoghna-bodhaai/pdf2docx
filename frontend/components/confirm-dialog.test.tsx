import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { ConfirmDialog } from "./confirm-dialog";
import { renderWithQuery } from "@/test/render";

function Harness() {
  const [open, setOpen] = useState(false);
  return <><button type="button" onClick={() => setOpen(true)}>Delete file</button><ConfirmDialog open={open} title="Delete paper.pdf?" description="This cannot be undone." onClose={() => setOpen(false)} onConfirm={vi.fn()} /></>;
}

describe("confirmation dialog", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() { this.open = true; };
    HTMLDialogElement.prototype.close = function close() { this.open = false; };
  });

  it("has an accessible name and returns focus to its trigger", async () => {
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    const trigger = screen.getByRole("button", { name: "Delete file" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Delete paper.pdf?" })).toHaveAttribute("aria-describedby", "confirm-description");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
