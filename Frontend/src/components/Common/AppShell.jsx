import Sidebar, { SidebarProvider } from "../Layout/Sidebar"

/**
 * Standard authenticated page layout: floating sidebar + scrollable content column.
 * `overlay` renders outside the scroll area (drawers, modals) so it is never clipped.
 */
export default function AppShell({ children, overlay = null, contentClassName = "" }) {
    return (
        <SidebarProvider>
            <div className="flex h-dvh overflow-hidden bg-white font-sans text-slate-900 antialiased">
                <Sidebar />
                <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
                    <main className={`flex-1 overflow-y-auto px-4 pb-10 pt-4 sm:px-6 sm:pt-6 lg:px-8 ${contentClassName}`}>
                        {children}
                    </main>
                </div>
                {overlay}
            </div>
        </SidebarProvider>
    )
}
