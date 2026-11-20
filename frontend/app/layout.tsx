import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Investment Intelligence",
  description: "Evidence-led investment research, committee decisions and thesis monitoring for US equities.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* apply saved theme before paint (no flash); storage access guarded */}
        <script dangerouslySetInnerHTML={{ __html: "try{var t=localStorage.getItem('ii_theme');if(t&&t!=='system')document.documentElement.setAttribute('data-theme',t)}catch(e){}" }} />
      </head>
      <body>
        <div className="shell">
          <Nav />
          <main className="main" id="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
