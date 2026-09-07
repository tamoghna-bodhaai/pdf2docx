"use client";
import { useEffect, type ReactNode } from "react";
import { useResponsiveDrawer } from "@/hooks/use-responsive-drawer";
export function ToolFrame({title, eyebrow, description, canvas, settings, actionFooter}: {title: string; eyebrow?: string; description: string; canvas: ReactNode; settings: ReactNode; actionFooter?: ReactNode}) {
  const { close: drawerClose, hidden: drawerHidden, mobile: drawerMobile, open: drawerOpen, panelRef: drawerPanelRef, show: drawerShow, triggerRef: drawerTriggerRef } = useResponsiveDrawer("(max-width: 1023px)");
  useEffect(() => { const show = () => drawerShow(); document.addEventListener("open-settings", show); return () => document.removeEventListener("open-settings", show); }, [drawerShow]);
  return <div className={`tool-workbench ${drawerOpen ? "dock-open" : ""}`}>
    <main className="workbench-canvas" inert={drawerMobile && drawerOpen || undefined}>
      <header className="canvas-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{description}</p></div></header>
      {canvas}
    </main>
    <div className="canvas-action-bar" hidden={drawerOpen}><button ref={drawerTriggerRef} type="button" aria-controls="context-dock" aria-expanded={drawerOpen} onClick={drawerShow}>Settings &amp; export</button></div>
    {drawerMobile && drawerOpen && <button className="dock-backdrop" type="button" aria-label="Close settings" onClick={() => drawerClose()} />}
    <aside ref={drawerPanelRef} className="context-dock" id="context-dock" role={drawerMobile ? "dialog" : undefined} aria-modal={drawerMobile && drawerOpen || undefined} aria-label="Tool settings" aria-hidden={drawerHidden || undefined} inert={drawerHidden || undefined}>
      <header className="settings-heading"><strong>Settings</strong>{drawerMobile && <button type="button" onClick={() => drawerClose()}>Close</button>}</header>
      <div className="dock-content">{settings}</div><footer className="action-footer">{actionFooter}</footer>
    </aside>
  </div>;
}
