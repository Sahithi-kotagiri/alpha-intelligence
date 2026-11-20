"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/analyze", label: "New analysis" },
  { href: "/screener", label: "S&P 500 screener" },
  { href: "/watchlist", label: "Watchlist & alerts" },
  { href: "/thesis", label: "Thesis monitor" },
  { href: "/history", label: "Historical decisions" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/performance", label: "Performance & evaluation" },
];

export default function Nav() {
  const path = usePathname();
  const active = (h: string) => (h === "/" ? path === "/" : path.startsWith(h) || (h === "/analyze" && path.startsWith("/analysis")));
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-title">Investment Intelligence</div>
        <div className="brand-sub">Evidence-led research · US equities</div>
      </div>
      <nav className="nav" aria-label="Main">
        {ITEMS.map((i) => (
          <Link key={i.href} href={i.href} className={active(i.href) ? "active" : ""} aria-current={active(i.href) ? "page" : undefined}>
            {i.label}
          </Link>
        ))}
        <div className="nav-sep" />
        <Link href="/settings" className={active("/settings") ? "active" : ""}>Settings & data sources</Link>
      </nav>
    </aside>
  );
}
