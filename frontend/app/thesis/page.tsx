"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { useApi } from "@/hooks/useApi";
import { ago } from "@/lib/format";
import { Card, Empty, Loading, RecBadge, Status } from "@/components/ui";

export default function ThesisList() {
  const { data, loading } = useApi<any>("/thesis");
  return (
    <>
      <div className="page-head"><div><h1>Thesis monitor</h1><p>Every analysis stores its thesis as testable assumptions. Monitoring re-tests them against new filings, results and prices.</p></div></div>
      {loading && <Loading />}
      {data && !data.theses.length && <Card><Empty title="No active theses"><p>Run an analysis to create one.</p><Link href="/analyze">New analysis →</Link></Empty></Card>}
      <div className="grid g2">
        {(data?.theses || []).map((t: any) => {
          const counts = t.assumptions.reduce((m: any, a: any) => ({ ...m, [a.status]: (m[a.status] || 0) + 1 }), {});
          return (
            <Card key={t.id} title={<Link href={`/thesis/${t.ticker}`}>{t.ticker} · {t.company_name}</Link>} sub={`Thesis v${t.version} · last checked ${t.last_checked_at ? ago(t.last_checked_at) : "not yet"}`}
              actions={<Status s={t.overall_status} />}>
              <div className="row small"><RecBadge rec={t.recommendation} />{Object.entries(counts).map(([k, v]) => <span key={k} className="muted">{String(v)} {k}</span>)}</div>
            </Card>);
        })}
      </div>
    </>
  );
}
