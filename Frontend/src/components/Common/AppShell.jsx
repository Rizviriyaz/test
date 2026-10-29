import Sidebar, { SidebarProvider } from "../Layout/Sidebar"

/**
 * Standard authenticated page layout: docked navigation rail + scrollable content column.
 * `overlay` renders outside the scroll area (drawers, modals) so it is never clipped.
 */
export default function AppShell({ children, overlay = null, contentClassName = "" }) {
    return (
        <SidebarProvider>
            <div className="flex h-dvh overflow-hidden bg-canvas font-sans text-slate-900 antialiased">
                <Sidebar />
                <div className="relative z-0 flex min-w-0 flex-1 flex-col overflow-hidden">
                    <main className={`flex-1 overflow-y-auto px-4 pb-10 pt-3.5 sm:px-6 sm:pt-4 lg:px-8 ${contentClassName}`}>
                        <div className="mx-auto w-full max-w-[1600px]">{children}</div>
                    </main>
                </div>
                {overlay}
            </div>
        </SidebarProvider>
    )
}
